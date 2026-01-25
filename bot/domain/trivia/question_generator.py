"""LLM-based trivia question generation with seed system."""

import re
from typing import Dict

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


async def generate_trivia_question(seed: str) -> Dict[str, str]:
    """
    Generate a trivia question using LLM with the given seed.

    Args:
        seed: Seed string in format "baseword_modifier"

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

            prompt = f"""Generate a trivia question that is at least somewhat related to this seed: {topic}/{context}

Requirements:
- Create a clear, factual trivia question with a single definitive answer
- the answer should be an interesting fact about the seed, not something obvious
- the seed itself should not be the answer to the question
- Choose the most appropriate category from: {', '.join(CATEGORIES)}
- Provide a brief explanation of the answer
- CRITICAL: Do not mention the answer or any part of it in the question text

Return in this EXACT format:
CATEGORY: [category name]
QUESTION: [Your question here]
ANSWER: [Correct answer - be specific but accept reasonable variations]
EXPLANATION: [2-3 sentence explanation]"""

            llm = ChatCompletionsClient.factory("gpt-4.1")
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

            category = category_match.group(1).strip()
            # Validate category is one of the allowed categories
            if category not in CATEGORIES:
                # Find closest match or default to first category
                category = CATEGORIES[0]
                logger.warning(f"Invalid category from LLM, defaulting to {category}")

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

                rewrite_llm = ChatCompletionsClient.factory("gpt-4.1")
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
