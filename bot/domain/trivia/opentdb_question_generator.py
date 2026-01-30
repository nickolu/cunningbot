"""
High-level wrapper for OpenTDB question generation with AI fallback.
"""

import logging
import random
from typing import Dict, List, Optional, Set, Tuple

from bot.api.opentdb.opentdb_client import OpenTDBClient
from bot.domain.trivia.question_seeds import get_unused_seed
from bot.domain.trivia.question_generator import generate_trivia_question

logger = logging.getLogger(__name__)

# OpenTDB category mapping
# Format: category_id: (opentdb_display_name, mapped_category)
OPENTDB_CATEGORIES = {
    9: ("General Knowledge", "History"),
    10: ("Entertainment: Books", "Arts & Literature"),
    11: ("Entertainment: Film", "Entertainment"),
    12: ("Entertainment: Music", "Entertainment"),
    13: ("Entertainment: Musicals & Theatres", "Entertainment"),
    14: ("Entertainment: Television", "Entertainment"),
    15: ("Entertainment: Video Games", "Entertainment"),
    16: ("Entertainment: Board Games", "Entertainment"),
    17: ("Science & Nature", "Science"),
    18: ("Science: Computers", "Science"),
    19: ("Science: Mathematics", "Science"),
    20: ("Mythology", "Arts & Literature"),
    21: ("Sports", "Sports"),
    22: ("Geography", "Geography"),
    23: ("History", "History"),
    24: ("Politics", "History"),
    25: ("Art", "Arts & Literature"),
    26: ("Celebrities", "Entertainment"),
    27: ("Animals", "Science"),
    28: ("Vehicles", "Science"),
    29: ("Entertainment: Comics", "Entertainment"),
    30: ("Science: Gadgets", "Science"),
    31: ("Entertainment: Japanese Anime & Manga", "Entertainment"),
    32: ("Entertainment: Cartoon & Animations", "Entertainment")
}


async def generate_trivia_questions_from_opentdb(
    easy_count: int,
    medium_count: int,
    hard_count: int,
    guild_id: Optional[str] = None,
    used_seeds: Optional[Set] = None,
    base_words: Optional[List[str]] = None,
    modifiers: Optional[List[str]] = None
) -> Tuple[List[Dict[str, str]], int]:
    """
    Generate trivia questions from OpenTDB API.

    All questions will be from the same randomly selected category.

    Args:
        easy_count: Number of easy questions
        medium_count: Number of medium questions
        hard_count: Number of hard questions
        guild_id: Guild ID for AI fallback
        used_seeds: Used seeds for AI fallback
        base_words: Base words for AI fallback
        modifiers: Modifiers for AI fallback

    Returns:
        Tuple of (questions, category_id)
        - questions: List of question dicts with standardized schema
        - category_id: The OpenTDB category ID that was selected

    Raises:
        Exception: If both OpenTDB and AI fallback fail
    """
    # Randomly select ONE category
    category_id = random.choice(list(OPENTDB_CATEGORIES.keys()))
    opentdb_name, mapped_category = OPENTDB_CATEGORIES[category_id]

    logger.info(f"Selected OpenTDB category: {opentdb_name} (ID: {category_id}, mapped to: {mapped_category})")

    client = OpenTDBClient()
    questions = []

    try:
        # Fetch questions by difficulty
        if easy_count > 0:
            easy_questions = await client.fetch_questions(
                amount=easy_count,
                category=category_id,
                difficulty="easy"
            )
            for q in easy_questions:
                questions.append({
                    "question": q["question"],
                    "correct_answer": q["correct_answer"],
                    "category": mapped_category,
                    "explanation": f"This is a {q['difficulty']} question from {opentdb_name}.",
                    "difficulty": q["difficulty"],
                    "source": "opentdb"
                })

        if medium_count > 0:
            medium_questions = await client.fetch_questions(
                amount=medium_count,
                category=category_id,
                difficulty="medium"
            )
            for q in medium_questions:
                questions.append({
                    "question": q["question"],
                    "correct_answer": q["correct_answer"],
                    "category": mapped_category,
                    "explanation": f"This is a {q['difficulty']} question from {opentdb_name}.",
                    "difficulty": q["difficulty"],
                    "source": "opentdb"
                })

        if hard_count > 0:
            hard_questions = await client.fetch_questions(
                amount=hard_count,
                category=category_id,
                difficulty="hard"
            )
            for q in hard_questions:
                questions.append({
                    "question": q["question"],
                    "correct_answer": q["correct_answer"],
                    "category": mapped_category,
                    "explanation": f"This is a {q['difficulty']} question from {opentdb_name}.",
                    "difficulty": q["difficulty"],
                    "source": "opentdb"
                })

        logger.info(f"Successfully generated {len(questions)} questions from OpenTDB")
        return questions, category_id

    except Exception as e:
        logger.warning(f"OpenTDB API failed: {e}. Falling back to AI generation.")
        return await _fallback_to_ai(
            easy_count + medium_count + hard_count,
            guild_id,
            used_seeds or set(),
            base_words,
            modifiers
        ), category_id


async def _fallback_to_ai(
    total_count: int,
    guild_id: Optional[str],
    used_seeds: Set,
    base_words: Optional[List[str]],
    modifiers: Optional[List[str]]
) -> List[Dict[str, str]]:
    """
    Generate questions using AI when OpenTDB fails.

    Args:
        total_count: Total number of questions to generate
        guild_id: Guild ID for seed tracking
        used_seeds: Set of already used seeds
        base_words: Optional base words for seed generation
        modifiers: Optional modifiers for seed generation

    Returns:
        List of AI-generated questions
    """
    logger.warning(f"Falling back to AI for {total_count} questions")

    questions = []
    for _ in range(total_count):
        seed = get_unused_seed(used_seeds, base_words, modifiers)
        q = await generate_trivia_question(seed)
        q["source"] = "ai"
        q["difficulty"] = None
        q["seed"] = seed
        questions.append(q)
        used_seeds.add(seed)

    logger.info(f"Generated {len(questions)} questions using AI fallback")
    return questions
