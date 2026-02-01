"""
High-level wrapper for OpenTDB question generation with AI fallback.
"""

import asyncio
import logging
import random
from typing import Dict, List, Optional, Set, Tuple

from bot.api.opentdb.opentdb_client import OpenTDBClient
from bot.domain.trivia.question_seeds import get_unused_seed
from bot.domain.trivia.question_generator import generate_trivia_question, generate_trivia_questions_batch

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
        # Fetch questions by difficulty with delays to avoid rate limiting
        # OpenTDB rate limits: 1 request per 5 seconds

        if easy_count > 0:
            logger.info(f"Fetching {easy_count} easy questions from OpenTDB")
            easy_questions = await client.fetch_questions(
                amount=easy_count,
                category=category_id,
                difficulty="easy"
            )
            for q in easy_questions:
                # Shuffle options: combine correct + incorrect, then randomize
                options = [q["correct_answer"]] + q.get("incorrect_answers", [])
                random.shuffle(options)

                questions.append({
                    "question": q["question"],
                    "correct_answer": q["correct_answer"],
                    "options": options,
                    "category": mapped_category,
                    "explanation": f"This is a {q['difficulty']} question from {opentdb_name}.",
                    "difficulty": q["difficulty"],
                    "source": "opentdb"
                })

            # Add delay if we need to fetch more questions
            if medium_count > 0 or hard_count > 0:
                logger.info("Waiting 6 seconds to avoid OpenTDB rate limit...")
                await asyncio.sleep(6)

        if medium_count > 0:
            logger.info(f"Fetching {medium_count} medium questions from OpenTDB")
            medium_questions = await client.fetch_questions(
                amount=medium_count,
                category=category_id,
                difficulty="medium"
            )
            for q in medium_questions:
                # Shuffle options: combine correct + incorrect, then randomize
                options = [q["correct_answer"]] + q.get("incorrect_answers", [])
                random.shuffle(options)

                questions.append({
                    "question": q["question"],
                    "correct_answer": q["correct_answer"],
                    "options": options,
                    "category": mapped_category,
                    "explanation": f"This is a {q['difficulty']} question from {opentdb_name}.",
                    "difficulty": q["difficulty"],
                    "source": "opentdb"
                })

            # Add delay if we need to fetch hard questions
            if hard_count > 0:
                logger.info("Waiting 6 seconds to avoid OpenTDB rate limit...")
                await asyncio.sleep(6)

        if hard_count > 0:
            logger.info(f"Fetching {hard_count} hard questions from OpenTDB")
            hard_questions = await client.fetch_questions(
                amount=hard_count,
                category=category_id,
                difficulty="hard"
            )
            for q in hard_questions:
                # Shuffle options: combine correct + incorrect, then randomize
                options = [q["correct_answer"]] + q.get("incorrect_answers", [])
                random.shuffle(options)

                questions.append({
                    "question": q["question"],
                    "correct_answer": q["correct_answer"],
                    "options": options,
                    "category": mapped_category,
                    "explanation": f"This is a {q['difficulty']} question from {opentdb_name}.",
                    "difficulty": q["difficulty"],
                    "source": "opentdb"
                })

        logger.info(f"✅ Successfully generated {len(questions)} questions from OpenTDB API")
        logger.info(f"   Category: {opentdb_name} (mapped to {mapped_category})")
        return questions, category_id

    except Exception as e:
        logger.error(f"❌ OpenTDB API failed: {e}")
        logger.warning(f"⚠️  Falling back to AI generation for {easy_count + medium_count + hard_count} questions")
        return await _fallback_to_ai(
            easy_count=easy_count,
            medium_count=medium_count,
            hard_count=hard_count,
            guild_id=guild_id,
            used_seeds=used_seeds or set(),
            base_words=base_words,
            modifiers=modifiers
        ), category_id


async def _fallback_to_ai(
    easy_count: int,
    medium_count: int,
    hard_count: int,
    guild_id: Optional[str],
    used_seeds: Set,
    base_words: Optional[List[str]],
    modifiers: Optional[List[str]]
) -> List[Dict[str, str]]:
    """
    Generate questions using AI when OpenTDB fails.

    Uses batch generation to create all questions in a single LLM call
    with a shared theme and specified difficulty distribution.

    Args:
        easy_count: Number of easy questions
        medium_count: Number of medium questions
        hard_count: Number of hard questions
        guild_id: Guild ID for seed tracking
        used_seeds: Set of already used seeds
        base_words: Optional base words for seed generation
        modifiers: Optional modifiers for seed generation

    Returns:
        List of AI-generated questions
    """
    total_count = easy_count + medium_count + hard_count
    logger.info(f"🤖 Generating {total_count} questions using AI fallback (batch mode)")
    logger.info(f"   Distribution: {easy_count} easy, {medium_count} medium, {hard_count} hard")

    # Generate a single seed for the entire batch (shared theme)
    seed = get_unused_seed(used_seeds, base_words, modifiers)
    used_seeds.add(seed)
    logger.info(f"   Using seed for batch: {seed}")

    # Generate all questions in a single LLM call
    questions = await generate_trivia_questions_batch(
        seed=seed,
        easy_count=easy_count,
        medium_count=medium_count,
        hard_count=hard_count
    )

    # Add metadata to each question
    for q in questions:
        q["source"] = "ai"
        q["seed"] = seed

    logger.info(f"✅ Successfully generated {len(questions)} questions using AI batch generation")
    return questions
