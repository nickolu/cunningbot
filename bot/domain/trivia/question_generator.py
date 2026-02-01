"""LLM-based trivia question generation with seed system."""

import json
import re
from typing import Dict, List

from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.domain.trivia.question_seeds import CATEGORIES
from bot.app.utils.logger import get_logger

logger = get_logger()

MAX_RETRIES = 3


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


async def generate_trivia_question(seed: str, category: str = None) -> Dict[str, str]:
    """
    Generate a trivia question using LLM with the given seed.

    Args:
        seed: Seed string in format "baseword_modifier"
        category: Optional category to constrain the question to (one of CATEGORIES)

    Returns:
        dict: {
            "question": str,
            "correct_answer": str,
            "category": str (one of CATEGORIES),
            "explanation": str
        }

    Raises:
        Exception: If generation fails after MAX_RETRIES attempts
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Generating trivia question for seed {seed} (attempt {attempt}/{MAX_RETRIES})")

            # Parse seed to extract context
            parts = seed.split("_")
            topic = parts[0].replace("_", " ")
            context = parts[1].replace("_", " ") if len(parts) > 1 else "general"

            # Build category instruction
            if category:
                category_instruction = f"- The question MUST be in the '{category}' category"
                category_format = f"CATEGORY: {category}"
            else:
                category_instruction = f"- Choose the most appropriate category from: {', '.join(CATEGORIES)}"
                category_format = "CATEGORY: [category name]"

            prompt = f"""Generate a trivia question that is at least somewhat related to this seed: {topic}/{context}

Requirements:
- Create a clear, factual trivia question with a single definitive answer
- the answer should be an interesting, non-obvious fact about the seed
- the question should be moderately challenging, not too easy but not impossible
- the seed itself should not be the answer to the question
{category_instruction}
- Provide a brief explanation of the answer
- CRITICAL: Do not mention the answer or any part of it in the question text

Return in this EXACT format:
{category_format}
QUESTION: [Your question here]
ANSWER: [Correct answer - be specific but accept reasonable variations]
EXPLANATION: [2-3 sentence explanation]"""

            llm = ChatCompletionsClient.factory("gpt-5.2")
            response = await llm.chat([
                {"role": "system", "content": "You are a trivia question creator. Follow the format exactly."},
                {"role": "user", "content": prompt}
            ])

            # Parse response with regex
            category_match = re.search(r'CATEGORY:\s*(.+)', response)
            question_match = re.search(r'QUESTION:\s*(.+)', response)
            answer_match = re.search(r'ANSWER:\s*(.+)', response)
            explanation_match = re.search(r'EXPLANATION:\s*(.+)', response, re.DOTALL)

            if not (category_match and question_match and answer_match):
                logger.warning(f"Failed to parse LLM response for seed {seed} on attempt {attempt}")
                last_error = ValueError(f"LLM response did not match expected format. Response: {response[:200]}...")
                continue

            # Use provided category or parse from LLM response
            if category:
                # Category was provided as parameter, use it
                parsed_category = category
            else:
                parsed_category = category_match.group(1).strip()
                # Validate category is one of the allowed categories
                if parsed_category not in CATEGORIES:
                    # Find closest match or default to first category
                    parsed_category = CATEGORIES[0]
                    logger.warning(f"Invalid category from LLM, defaulting to {parsed_category}")

            category = parsed_category

            question = question_match.group(1).strip()
            answer = answer_match.group(1).strip()
            explanation = explanation_match.group(1).strip() if explanation_match else ""

            # Check if answer appears in question - if so, rewrite it
            if answer_appears_in_question(question, answer):
                logger.warning(f"Answer appears in question for seed {seed}, rewriting question")
                rewrite_prompt = f"""The following trivia question contains the answer within it.
Rewrite the question to remove any reference to the answer while keeping it challenging and factually accurate.

Original Question: {question}
Answer: {answer}
Category: {category}

Return ONLY the rewritten question, nothing else."""

                rewrite_llm = ChatCompletionsClient.factory("gpt-5.2")
                rewritten_question = await rewrite_llm.chat([
                    {"role": "system", "content": "You are a trivia question editor. Rewrite questions to avoid giving away the answer."},
                    {"role": "user", "content": rewrite_prompt}
                ])

                question = rewritten_question.strip()
                logger.info(f"Question rewritten for seed {seed}")

            # Success! Return the parsed question
            logger.info(f"Successfully generated trivia question for seed {seed}")
            return {
                "question": question,
                "correct_answer": answer,
                "category": category,
                "explanation": explanation
            }

        except Exception as e:
            logger.error(f"Error generating trivia question for seed {seed} on attempt {attempt}: {e}")
            last_error = e
            if attempt < MAX_RETRIES:
                logger.info(f"Retrying... ({attempt}/{MAX_RETRIES})")
                continue

    # If we get here, all retries failed
    error_msg = f"Failed to generate trivia question after {MAX_RETRIES} attempts. Last error: {last_error}"
    logger.error(error_msg)
    raise Exception(error_msg)


async def generate_trivia_questions_batch(
    seed: str,
    easy_count: int,
    medium_count: int,
    hard_count: int
) -> List[Dict[str, str]]:
    """
    Generate multiple trivia questions in a single LLM call with specified difficulty distribution.

    All questions will share the same thematic seed but cover different aspects.

    Args:
        seed: Seed string in format "baseword_modifier"
        easy_count: Number of easy questions
        medium_count: Number of medium questions
        hard_count: Number of hard questions

    Returns:
        list of dicts: [{
            "question": str,
            "correct_answer": str,
            "category": str (one of CATEGORIES),
            "explanation": str,
            "difficulty": str (easy/medium/hard)
        }, ...]

    Raises:
        Exception: If generation fails after MAX_RETRIES attempts
    """
    total_count = easy_count + medium_count + hard_count

    if total_count == 0:
        logger.warning("No questions requested in batch generation")
        return []

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Batch generating {total_count} trivia questions for seed {seed} (attempt {attempt}/{MAX_RETRIES})")

            # Parse seed to extract context
            parts = seed.split("_")
            topic = parts[0].replace("_", " ")
            context = parts[1].replace("_", " ") if len(parts) > 1 else "general"

            # Build difficulty specification
            difficulty_spec = []
            if easy_count > 0:
                difficulty_spec.append(f"- {easy_count} easy question(s)")
            if medium_count > 0:
                difficulty_spec.append(f"- {medium_count} medium question(s)")
            if hard_count > 0:
                difficulty_spec.append(f"- {hard_count} hard question(s)")

            difficulty_requirements = "\n".join(difficulty_spec)

            prompt = f"""Generate {total_count} trivia questions about the theme: {topic}/{context}

Difficulty distribution:
{difficulty_requirements}

Requirements for ALL questions:
- All questions should relate to the theme "{topic}/{context}" but cover different aspects
- Each question should have a clear, factual answer
- Answers should be interesting, non-obvious facts
- Questions should be appropriately challenging for their difficulty level
- Easy: Basic facts that most people might know
- Medium: Requires some knowledge or reasoning
- Hard: Obscure facts or requires deep knowledge
- The seed itself should not be the answer
- Choose the most appropriate category from: {', '.join(CATEGORIES)}
- CRITICAL: Do not mention the answer or any part of it in the question text
- Ensure variety - don't repeat similar questions

Return a JSON array with this EXACT structure:
[
  {{
    "question": "Your question here",
    "correct_answer": "Specific answer",
    "category": "Category from the list",
    "explanation": "2-3 sentence explanation",
    "difficulty": "easy|medium|hard"
  }},
  ...
]

Return ONLY the JSON array, no other text."""

            llm = ChatCompletionsClient.factory("gpt-5.2")
            response = await llm.chat([
                {"role": "system", "content": "You are a trivia question creator. Return only valid JSON."},
                {"role": "user", "content": prompt}
            ])

            # Parse JSON response
            # Sometimes LLMs wrap JSON in markdown code blocks, so clean that up
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
                logger.warning(f"Failed to parse JSON response for seed {seed} on attempt {attempt}: {e}")
                logger.warning(f"Response was: {response[:500]}")
                last_error = ValueError(f"Invalid JSON response: {e}")
                continue

            if not isinstance(questions_data, list):
                logger.warning(f"JSON response is not a list for seed {seed} on attempt {attempt}")
                last_error = ValueError("Response is not a JSON array")
                continue

            if len(questions_data) != total_count:
                logger.warning(f"Expected {total_count} questions but got {len(questions_data)} for seed {seed}")
                # We'll accept it if we got at least some questions
                if len(questions_data) == 0:
                    last_error = ValueError(f"No questions generated")
                    continue

            # Validate and normalize each question
            validated_questions = []
            for i, q_data in enumerate(questions_data):
                try:
                    # Extract fields
                    question = q_data.get("question", "").strip()
                    answer = q_data.get("correct_answer", "").strip()
                    category = q_data.get("category", "").strip()
                    explanation = q_data.get("explanation", "").strip()
                    difficulty = q_data.get("difficulty", "medium").strip().lower()

                    # Validate required fields
                    if not question or not answer:
                        logger.warning(f"Question {i+1} missing required fields, skipping")
                        continue

                    # Validate category
                    if category not in CATEGORIES:
                        category = CATEGORIES[0]
                        logger.warning(f"Question {i+1}: Invalid category, defaulting to {category}")

                    # Validate difficulty
                    if difficulty not in ["easy", "medium", "hard"]:
                        difficulty = "medium"
                        logger.warning(f"Question {i+1}: Invalid difficulty, defaulting to medium")

                    # Check if answer appears in question
                    if answer_appears_in_question(question, answer):
                        logger.warning(f"Question {i+1}: Answer appears in question, marking for review")
                        # We'll accept it but log the warning - fixing it would require additional API calls

                    validated_questions.append({
                        "question": question,
                        "correct_answer": answer,
                        "category": category,
                        "explanation": explanation,
                        "difficulty": difficulty
                    })

                except Exception as e:
                    logger.warning(f"Error validating question {i+1} for seed {seed}: {e}")
                    continue

            if len(validated_questions) == 0:
                logger.warning(f"No valid questions after validation for seed {seed} on attempt {attempt}")
                last_error = ValueError("No valid questions after validation")
                continue

            # Success!
            logger.info(f"Successfully generated {len(validated_questions)} trivia questions for seed {seed}")
            logger.info(f"  Breakdown: {sum(1 for q in validated_questions if q['difficulty'] == 'easy')} easy, "
                       f"{sum(1 for q in validated_questions if q['difficulty'] == 'medium')} medium, "
                       f"{sum(1 for q in validated_questions if q['difficulty'] == 'hard')} hard")
            return validated_questions

        except Exception as e:
            logger.error(f"Error in batch generation for seed {seed} on attempt {attempt}: {e}")
            last_error = e
            if attempt < MAX_RETRIES:
                logger.info(f"Retrying... ({attempt}/{MAX_RETRIES})")
                continue

    # If we get here, all retries failed
    error_msg = f"Failed to batch generate trivia questions after {MAX_RETRIES} attempts. Last error: {last_error}"
    logger.error(error_msg)
    raise Exception(error_msg)
