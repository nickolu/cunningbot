"""LLM-based trivia question generation with seed system."""

import json
import re
from typing import Dict, List, Optional

from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.app.utils.logger import get_logger

logger = get_logger()

MAX_RETRIES = 3
LLM_MODEL = "gpt-5.2"


def normalize_text(text: str) -> str:
    """Remove spaces and special characters, lowercase."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def answer_appears_in_question(question: str, answer: str) -> bool:
    """
    Check if any significant part of the answer appears in the question.
    Splits answer into words and checks each significant word.
    """
    normalized_question = normalize_text(question)

    # Split answer into words and check each significant word (length > 3)
    answer_words = answer.split()
    for word in answer_words:
        if len(word) > 3:  # Only check significant words
            normalized_word = normalize_text(word)
            if normalized_word in normalized_question:
                return True

    return False


async def _gather_facts(seed: str, category: str) -> str:
    """
    Phase 1: Ask the LLM for interesting facts about the seed topic.

    Args:
        seed: Seed string in format "topic :: modifier"
        category: Category for context

    Returns:
        Raw facts text from the LLM

    Raises:
        Exception: On LLM failure (caller handles retry)
    """
    parts = seed.split(" :: ")
    topic = parts[0].strip()
    context = parts[1].strip() if len(parts) > 1 else "general"

    prompt = f"""Tell me 20 interesting facts about "{topic}" (angle: {context}, category: {category}).

Include a mix of:
- Well-known facts that most people would recognize (label these [EASY])
- Moderately known facts that someone familiar with the topic would know (label these [MEDIUM])
- Obscure or surprising facts that would challenge even enthusiasts (label these [HARD])

Focus on facts that are interesting, surprising, or fun — not dry statistics.
Each fact should be specific enough that a good trivia question could be built from it.

Return as a numbered list with difficulty tags."""

    llm = ChatCompletionsClient.factory(LLM_MODEL)
    response = await llm.chat([
        {"role": "system", "content": "You are a trivia research assistant. Provide specific, verifiable, interesting facts."},
        {"role": "user", "content": prompt}
    ])

    return response


async def _generate_from_facts(
    facts: str,
    category: str,
    easy_count: int,
    medium_count: int,
    hard_count: int,
    context_questions: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """
    Phase 2: Generate trivia questions from pre-gathered facts.

    Args:
        facts: Raw facts text from _gather_facts()
        category: Category for the questions
        easy_count: Number of easy questions to generate
        medium_count: Number of medium questions to generate
        hard_count: Number of hard questions to generate
        context_questions: Optional list of existing questions to avoid duplicating

    Returns:
        List of validated question dicts

    Raises:
        Exception: If JSON parsing fails or no valid questions are produced
    """
    # Build difficulty spec string
    difficulty_parts = []
    if easy_count > 0:
        difficulty_parts.append(f"- {easy_count} easy questions")
    if medium_count > 0:
        difficulty_parts.append(f"- {medium_count} medium questions")
    if hard_count > 0:
        difficulty_parts.append(f"- {hard_count} hard questions")
    difficulty_spec = "\n".join(difficulty_parts)

    # Build optional context section
    context_section = ""
    if context_questions:
        context_lines = []
        for i, q in enumerate(context_questions, 1):
            q_text = q.get("question", "")
            a_text = q.get("correct_answer", "")
            if q_text:
                context_lines.append(f"{i}. Q: {q_text}" + (f" | A: {a_text}" if a_text else ""))
        if context_lines:
            context_section = (
                "\nThese questions are already in the same trivia set. Your questions should complement them "
                "but NOT duplicate or closely overlap:\n" + "\n".join(context_lines)
            )

    prompt = f"""Using the facts below, create trivia questions for the category "{category}".

Facts:
{facts}

Generate:
{difficulty_spec}

Requirements:
- Each question must be based on one or more of the facts above
- CRITICAL: The answer must NOT appear anywhere in the question text
- Easy questions: test common knowledge that most people would know
- Medium questions: require some familiarity with the topic
- Hard questions: test deep or obscure knowledge
- Answers should be specific (a name, place, thing, concept, etc.)
- Each question needs a 2-3 sentence explanation
- All questions should be in the "{category}" category
{context_section}

Return a JSON array with this EXACT structure:
[
  {{
    "question": "Your question here",
    "correct_answer": "Specific answer",
    "category": "{category}",
    "explanation": "2-3 sentence explanation",
    "difficulty": "easy|medium|hard"
  }}
]

Return ONLY the JSON array, no other text."""

    llm = ChatCompletionsClient.factory(LLM_MODEL)
    response = await llm.chat([
        {"role": "system", "content": "You are a trivia question creator. Create questions where the answer is NOT mentioned or hinted at in the question text. Return only valid JSON."},
        {"role": "user", "content": prompt}
    ])

    # Strip markdown code block wrapping if present
    response_clean = response.strip()
    if response_clean.startswith("```json"):
        response_clean = response_clean[7:]
    if response_clean.startswith("```"):
        response_clean = response_clean[3:]
    if response_clean.endswith("```"):
        response_clean = response_clean[:-3]
    response_clean = response_clean.strip()

    try:
        questions_data = json.loads(response_clean)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON response: {e}. Response was: {response[:500]}")

    if not isinstance(questions_data, list):
        raise ValueError("Response is not a JSON array")

    # Validate and normalize each question
    validated_questions = []
    for i, q_data in enumerate(questions_data):
        try:
            question = q_data.get("question", "").strip()
            answer = q_data.get("correct_answer", "").strip()
            explanation = q_data.get("explanation", "").strip()
            difficulty = q_data.get("difficulty", "medium").strip().lower()

            # Validate required fields
            if not question or not answer:
                logger.warning(f"Question {i + 1} missing required fields, skipping")
                continue

            # Override category with the provided one
            validated_category = category

            # Validate difficulty
            if difficulty not in ["easy", "medium", "hard"]:
                logger.warning(f"Question {i + 1}: Invalid difficulty '{difficulty}', defaulting to medium")
                difficulty = "medium"

            # Check if answer appears in question — log warning but accept
            if answer_appears_in_question(question, answer):
                logger.warning(f"Question {i + 1}: Answer appears in question text")

            validated_questions.append({
                "question": question,
                "correct_answer": answer,
                "category": validated_category,
                "explanation": explanation,
                "difficulty": difficulty,
            })

        except Exception as e:
            logger.warning(f"Error validating question {i + 1}: {e}")
            continue

    if len(validated_questions) == 0:
        raise ValueError("No valid questions after validation")

    return validated_questions


async def generate_trivia_questions(
    seed: str,
    category: str,
    easy_count: int = 0,
    medium_count: int = 0,
    hard_count: int = 0,
    context_questions: Optional[List[Dict[str, str]]] = None,
) -> List[Dict[str, str]]:
    """
    Generate trivia questions using a two-step pipeline:
    1. Gather interesting facts about the seed topic
    2. Generate questions from those facts

    Args:
        seed: Seed string in format "topic :: modifier"
        category: Category for the questions (one of the 24 OpenTDB categories)
        easy_count: Number of easy questions to generate
        medium_count: Number of medium questions
        hard_count: Number of hard questions
        context_questions: Optional list of existing questions to avoid duplicating

    Returns:
        List of question dicts with keys: question, correct_answer, category, explanation, difficulty

    Raises:
        Exception: If generation fails after MAX_RETRIES attempts
    """
    total_count = easy_count + medium_count + hard_count

    if total_count == 0:
        return []

    facts = None
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        # Phase 1: Gather facts (only if we don't have them yet)
        if facts is None:
            try:
                facts = await _gather_facts(seed, category)
                logger.info(f"Successfully gathered facts for seed '{seed}' (attempt {attempt})")
            except Exception as e:
                logger.error(f"Facts gathering failed for seed '{seed}' (attempt {attempt}): {e}")
                last_error = e
                continue

        # Phase 2: Generate questions from facts
        try:
            questions = await _generate_from_facts(facts, category, easy_count, medium_count, hard_count, context_questions)
            if len(questions) == 0:
                raise ValueError("No valid questions generated")

            if len(questions) < total_count:
                logger.warning(f"Expected {total_count} questions but got {len(questions)} for seed '{seed}'")

            logger.info(f"Successfully generated {len(questions)} questions for seed '{seed}'")
            return questions
        except Exception as e:
            logger.error(f"Question generation failed for seed '{seed}' (attempt {attempt}): {e}")
            last_error = e
            # Don't reset facts — retry step 2 with same facts
            continue

    raise Exception(f"Failed to generate trivia questions after {MAX_RETRIES} attempts. Last error: {last_error}")
