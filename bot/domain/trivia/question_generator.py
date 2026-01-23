"""LLM-based trivia question generation with seed system."""

import re
from typing import Dict

from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.domain.trivia.question_seeds import CATEGORIES
from bot.app.utils.logger import get_logger

logger = get_logger()

MAX_RETRIES = 3


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

            prompt = f"""Generate a trivia question based on this seed:
Topic: {topic}
Context: {context}

Requirements:
- Create a clear, factual question with a single definitive answer
- The question should be moderately challenging and not have an obvious answer
- Avoid basic/common knowledge that most people would know
- Focus on interesting details, connections, or lesser-known facts
- The question should be engaging and educational
- The question should have an objective answer
- Choose the most appropriate category from: {', '.join(CATEGORIES)}
- Provide a brief explanation of the answer

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

            # Success! Return the parsed question
            logger.info(f"Successfully generated trivia question for seed {seed}")
            return {
                "question": question_match.group(1).strip(),
                "correct_answer": answer_match.group(1).strip(),
                "category": category,
                "explanation": explanation_match.group(1).strip() if explanation_match else ""
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
