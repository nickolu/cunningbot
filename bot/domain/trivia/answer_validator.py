"""LLM-based answer validation for trivia questions."""

import json
import re
from typing import Dict

from bot.api.openai.chat_completions_client import ChatCompletionsClient
from bot.app.utils.logger import get_logger

logger = get_logger()


async def validate_answer(user_answer: str, correct_answer: str, question: str, options: list = None) -> Dict[str, any]:
    """
    Validate if user's answer is correct.

    For multiple choice questions (with options), uses exact string matching.
    For open-ended questions (AI-generated), uses LLM validation.

    Args:
        user_answer: The answer submitted by the user
        correct_answer: The correct answer to the question
        question: The original question for context
        options: Optional list of answer options (indicates multiple choice)

    Returns:
        dict: {
            "is_correct": bool,
            "feedback": str
        }
    """
    # For multiple choice questions, use exact string matching (case-insensitive)
    if options and len(options) > 0:
        user_lower = user_answer.strip().lower()
        correct_lower = correct_answer.strip().lower()

        # Check if answer matches the correct answer
        if user_lower == correct_lower:
            return {
                "is_correct": True,
                "feedback": "Exact match"
            }

        # Check if answer matches any of the options (and it matches correct answer)
        for option in options:
            if user_lower == option.strip().lower():
                # User selected a valid option, check if it's correct
                is_correct = (user_lower == correct_lower)
                return {
                    "is_correct": is_correct,
                    "feedback": "Exact match" if is_correct else "Incorrect option"
                }

        # User's answer doesn't match any option
        return {
            "is_correct": False,
            "feedback": "Answer does not match any option"
        }

    # For AI questions (no options), use LLM validation
    try:
        prompt = f"""Evaluate if the user's answer is correct.

Question: {question}
Correct Answer: {correct_answer}
User's Answer: {user_answer}

Consider:
- Exact matches (1914 = 1914)
- Semantic equivalence (WW1 = World War 1 = First World War)
- Minor spelling errors (Shakespere = Shakespeare)
- Reasonable variations (USA = United States = America)

Reject:
- Completely wrong answers
- Opposite answers
- Off-by-one errors for dates/numbers (1914 ≠ 1915)

Return JSON: {{"is_correct": true/false, "feedback": "brief explanation"}}"""

        llm = ChatCompletionsClient.factory("gpt-4o-mini")
        response = await llm.chat([
            {
                "role": "system",
                "content": "You are a fair trivia judge. Be lenient with formatting but strict with facts. Return only JSON."
            },
            {"role": "user", "content": prompt}
        ])

        # Parse JSON from response
        json_match = re.search(r'\{[^}]+\}', response, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            return {
                "is_correct": bool(result.get("is_correct", False)),
                "feedback": str(result.get("feedback", ""))
            }

    except Exception as e:
        logger.error(f"Error validating answer: {e}")

    # Fallback: exact string match
    is_match = user_answer.strip().lower() == correct_answer.strip().lower()
    return {
        "is_correct": is_match,
        "feedback": "Validation error - using exact match"
    }
