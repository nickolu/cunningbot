"""Shared trivia answer submission logic."""

import datetime as dt
import discord
import re

from bot.app.app_state import (
    set_state_value_from_interaction,
    get_state_value_from_interaction,
)
from bot.app.redis.trivia_store import TriviaRedisStore
from bot.domain.trivia.answer_validator import validate_answer
from bot.app.utils.logger import get_logger

logger = get_logger()


def parse_batch_answers(answer_text: str) -> dict[str, str]:
    """Parse multi-line answer format into dict.

    Input examples:
        "1. b\\n2. c\\n3. The Taj Mahal"
        "1.b\\n2. c\\n3. answer"
        "1 b\\n2 c\\n3 text"

    Output:
        {"1": "b", "2": "c", "3": "The Taj Mahal"}

    Handles variations:
    - "1.b", "1. b", "1 b"
    - Extra whitespace, blank lines
    - Case-insensitive

    Returns empty dict if parsing fails.
    """
    answers = {}

    # Match lines like: "1. answer" or "1 answer" or "1.answer"
    # Pattern: optional whitespace, digit(s), optional dot, optional whitespace, then capture everything else
    pattern = r'^\s*(\d+)\s*\.?\s*(.+?)\s*$'

    for line in answer_text.split('\n'):
        line = line.strip()
        if not line:
            continue

        match = re.match(pattern, line)
        if match:
            question_num = match.group(1)
            answer = match.group(2).strip()

            # Handle duplicate question numbers - take last occurrence
            answers[question_num] = answer
        else:
            # Line doesn't match expected format
            logger.warning(f"Line doesn't match answer format: '{line}'")

    return answers


async def validate_batch_answers(
    answers: dict[str, str],
    questions: dict[str, dict],
    answer_maps: dict[str, dict]
) -> dict[str, dict]:
    """Validate each answer in the batch.

    Args:
        answers: User's answers {q_num: answer_text}
        questions: Question data {q_num: question_data}
        answer_maps: Letter mappings {q_num: answer_map}

    Returns:
        {
            "1": {"answer": "b", "mapped_answer": "Paris", "is_correct": True, "feedback": "..."},
            "2": {"answer": "c", "mapped_answer": "1945", "is_correct": True, "feedback": "..."},
            ...
        }

    For each question:
    - Map letters to full text if multiple choice
    - Call validate_answer() with options
    - Collect results
    """
    validated = {}

    for q_num, question_data in questions.items():
        user_answer_raw = answers.get(q_num, "").strip()

        # Map letter answers to actual text for multiple choice questions
        answer_map = answer_maps.get(q_num, {})
        mapped_answer = user_answer_raw

        if answer_map and len(user_answer_raw) == 1:
            # Check if it's a valid option letter (case-insensitive)
            letter = user_answer_raw.upper()
            if letter in answer_map:
                mapped_answer = answer_map[letter]
                logger.info(f"Mapped letter answer '{letter}' to '{mapped_answer}' for Q{q_num}")

        # Validate the answer
        correct_answer = question_data.get("correct_answer", "")
        question_text = question_data.get("question", "")
        options = question_data.get("options", [])

        try:
            validation_result = await validate_answer(mapped_answer, correct_answer, question_text, options)

            validated[q_num] = {
                "answer": user_answer_raw,
                "mapped_answer": mapped_answer,
                "is_correct": validation_result["is_correct"],
                "feedback": validation_result.get("feedback", "")
            }
        except Exception as e:
            logger.error(f"Failed to validate answer for Q{q_num}: {e}")
            validated[q_num] = {
                "answer": user_answer_raw,
                "mapped_answer": mapped_answer,
                "is_correct": False,
                "feedback": f"Validation error: {str(e)}"
            }

    return validated


def format_batch_feedback(
    validated_answers: dict[str, dict],
    questions: dict[str, dict]
) -> str:
    """Format feedback message showing results for all questions.

    Output example:
        "You got 2/3 questions correct!

        Here are the correct answers:

        1. **B. Paris** ✅ (you answered B)
        2. **C. 1982** ✅ (you answered C)
        3. **Burj Khalifa** ❌ (you answered 'Taj Mahal')

    Shows:
    - Score at top
    - Each question with correct answer
    - Checkmark/X for user's answer
    - NO explanations in immediate feedback
    """
    # Calculate score
    total = len(questions)
    correct_count = sum(1 for v in validated_answers.values() if v.get("is_correct", False))

    # Build feedback message
    lines = [f"You got **{correct_count}/{total}** questions correct!\n"]

    if correct_count == total:
        lines.append("🎉 **Perfect score!** Amazing work!\n")
    elif correct_count == 0:
        lines.append("Keep trying! Every quiz helps you learn more.\n")

    lines.append("**Here are the correct answers:**\n")

    # Sort question numbers numerically
    sorted_q_nums = sorted(questions.keys(), key=lambda x: int(x))

    for q_num in sorted_q_nums:
        question_data = questions[q_num]
        validation = validated_answers.get(q_num, {})

        correct_answer = question_data.get("correct_answer", "")
        user_answer = validation.get("answer", "")
        is_correct = validation.get("is_correct", False)

        # Determine if this is multiple choice
        options = question_data.get("options", [])
        answer_map = question_data.get("answer_map", {})

        # Find the letter for the correct answer (if multiple choice)
        correct_letter = None
        if answer_map:
            for letter, text in answer_map.items():
                if text.strip().lower() == correct_answer.strip().lower():
                    correct_letter = letter
                    break

        # Format the correct answer display
        if correct_letter:
            correct_display = f"**{correct_letter}. {correct_answer}**"
        else:
            correct_display = f"**{correct_answer}**"

        # Format user's answer display
        if is_correct:
            status = "✅"
            user_display = user_answer if not correct_letter else user_answer.upper()
        else:
            status = "❌"
            if not user_answer:
                user_display = "no answer"
            else:
                user_display = f"'{user_answer}'" if not correct_letter else user_answer.upper()

        lines.append(f"{q_num}. {correct_display} {status} (you answered {user_display})")

    return "\n".join(lines)


async def post_ai_explanation_followup(
    channel: discord.TextChannel,
    thread: discord.Thread,
    questions: dict[str, dict],
    batch_id: str
) -> None:
    """Post detailed explanation for AI questions after game ends.

    Called by game closer task.
    Posts in the thread with format:

        "🤖 **AI Question Deep Dive**

        Question 3: The Taj Mahal is located in which country?
        **Answer:** India

        **Detailed Explanation:**
        The Taj Mahal is an ivory-white marble mausoleum on the
        right bank of the river Yamuna in Agra, Uttar Pradesh, India..."

    Only posts if batch contains AI questions.
    """
    ai_questions = {
        q_num: q_data
        for q_num, q_data in questions.items()
        if not q_data.get("options") or len(q_data.get("options", [])) == 0
    }

    if not ai_questions:
        return

    lines = ["🤖 **AI Question Deep Dive**\n"]

    sorted_q_nums = sorted(ai_questions.keys(), key=lambda x: int(x))

    for q_num in sorted_q_nums:
        q_data = ai_questions[q_num]
        question_text = q_data.get("question", "")
        correct_answer = q_data.get("correct_answer", "")
        explanation = q_data.get("explanation", "")

        lines.append(f"**Question {q_num}:** {question_text}")
        lines.append(f"**Answer:** {correct_answer}\n")

        if explanation:
            lines.append(f"**Detailed Explanation:**")
            lines.append(f"{explanation}\n")

    message = "\n".join(lines)

    try:
        if thread:
            await thread.send(message)
        else:
            await channel.send(message)
        logger.info(f"Posted AI explanation followup for batch {batch_id[:8]}")
    except Exception as e:
        logger.error(f"Failed to post AI explanation: {e}")


async def update_question_stats(
    bot: discord.ext.commands.Bot,
    guild_id: str,
    game_id: str,
    game_data: dict,
    store: TriviaRedisStore
) -> None:
    """Update the question message with current answer statistics.

    Args:
        bot: The Discord bot instance
        guild_id: Guild ID as string
        game_id: Game ID
        game_data: Game data containing message_id and channel_id
        store: TriviaRedisStore instance
    """
    # Import here to avoid circular dependency
    from bot.app.commands.trivia.trivia import create_question_embed, count_submissions

    message_id = game_data.get("message_id")
    channel_id = game_data.get("channel_id")

    if not message_id or not channel_id:
        logger.warning(f"Missing message_id or channel_id for game {game_id[:8]}")
        return

    # Get the channel and message
    channel = bot.get_channel(channel_id)
    if not channel:
        logger.warning(f"Could not find channel {channel_id}")
        return

    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        logger.warning(f"Could not find message {message_id}")
        return
    except discord.Forbidden:
        logger.warning(f"No permission to fetch message {message_id}")
        return

    # Get all submissions and count stats
    submissions = await store.get_submissions(guild_id, game_id)
    stats = count_submissions(submissions)

    # Recreate the embed with stats
    ends_at = dt.datetime.fromisoformat(game_data["ends_at"])
    updated_embed = create_question_embed(
        question_data=game_data,
        game_id=game_id,
        ends_at=ends_at,
        stats=stats
    )

    # Update the message
    try:
        await message.edit(embed=updated_embed)
        logger.info(f"Updated question stats for game {game_id[:8]}: {stats}")
    except discord.Forbidden:
        logger.warning(f"No permission to edit message {message_id}")
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")


async def submit_trivia_answer(
    bot: discord.ext.commands.Bot,
    interaction: discord.Interaction,
    answer_text: str,
    guild_id: str,
    game_id: str = None
) -> None:
    """
    Submit an answer to an active trivia game using Redis (atomic, no race conditions).

    Args:
        bot: The Discord bot instance
        interaction: The Discord interaction (from slash command or modal)
                     NOTE: This interaction should already be deferred by the caller
        answer_text: The user's answer
        guild_id: The guild ID as a string
        game_id: Optional game ID to submit to (if known)
    """
    # NOTE: Interaction should already be deferred by caller (modal on_submit or slash command)
    # We use followup.send() for all responses
    store = TriviaRedisStore()

    # Find active game for this channel
    active_games = await store.get_active_games(guild_id)

    game_data = None

    # If game_id is provided, use it directly
    if game_id:
        game_data = active_games.get(game_id)
        if not game_data:
            await interaction.followup.send(
                "❌ This trivia game is no longer active.", ephemeral=True
            )
            return
    else:
        # Find game by thread_id or channel_id
        channel_id = interaction.channel.id

        for gid, gdata in active_games.items():
            if gdata.get("thread_id") == channel_id or gdata.get("channel_id") == channel_id:
                game_id = gid
                game_data = gdata
                break

    if not game_id or not game_data:
        await interaction.followup.send(
            "❌ No active trivia game found for this channel.", ephemeral=True
        )
        return

    # Prepare submission data
    user_id_str = str(interaction.user.id)

    # Map letter answers to actual text for multiple choice questions
    user_answer = answer_text.strip()
    answer_map = game_data.get("answer_map", {})
    if answer_map and len(user_answer) == 1:
        # Check if it's a valid option letter (case-insensitive)
        letter = user_answer.upper()
        if letter in answer_map:
            user_answer = answer_map[letter]
            logger.info(f"Mapped letter answer '{letter}' to '{user_answer}' for user {user_id_str}")

    submission_data = {
        "answer": user_answer,
        "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "is_correct": None,  # Will be set by validation
        "feedback": None,
        "validated_at": None
    }

    # Validate answer immediately (for user feedback)
    correct_answer = game_data.get("correct_answer", "")
    question = game_data.get("question", "")
    options = game_data.get("options", [])

    try:
        logger.info(f"Validating answer for user {user_id_str} in game {game_id[:8]}")
        validation_result = await validate_answer(user_answer, correct_answer, question, options)

        # Update submission with validation results
        submission_data["is_correct"] = validation_result["is_correct"]
        submission_data["feedback"] = validation_result.get("feedback", "")
        submission_data["validated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()

    except Exception as e:
        logger.warning(f"Failed to validate answer immediately: {e}")
        # Continue with unvalidated submission

    # Submit atomically using Lua script
    result = await store.submit_answer_atomic(
        guild_id, game_id, user_id_str, submission_data
    )

    # Handle result
    if result.get("err"):
        error_code = result["err"]

        if error_code == "GAME_NOT_FOUND":
            await interaction.followup.send(
                "❌ This trivia game is no longer active.", ephemeral=True
            )
        elif error_code == "GAME_CLOSED":
            await interaction.followup.send(
                "❌ This game has already been closed.", ephemeral=True
            )
        elif error_code == "WINDOW_CLOSED":
            await interaction.followup.send(
                "❌ The answer window has closed. Wait for results!", ephemeral=True
            )
        elif error_code == "ALREADY_SUBMITTED":
            await interaction.followup.send(
                "❌ You have already submitted an answer to this trivia question.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Failed to submit answer. Please try again.", ephemeral=True
            )
        return

    # Success! Send feedback based on validation
    if submission_data.get("is_correct"):
        feedback_message = (
            "✅ **Correct!** Your answer has been recorded.\n\n"
            "You'll see the official results when the answer window closes."
        )
    elif submission_data.get("is_correct") is False:
        explanation = game_data.get("explanation", "")
        feedback_message = (
            f"❌ **Sorry, that's not correct.**\n\n"
            f"The correct answer is: **{correct_answer}**"
        )
        if explanation:
            feedback_message += f"\n\n{explanation}"
    else:
        # Validation failed, generic message
        feedback_message = (
            "✅ Your answer has been recorded!\n\n"
            "We'll validate it when the answer window closes."
        )

    await interaction.followup.send(feedback_message, ephemeral=True)

    # Update the question message with latest stats
    try:
        await update_question_stats(bot, guild_id, game_id, game_data, store)
    except Exception as e:
        logger.warning(f"Failed to update question stats: {e}")


async def update_batch_question_stats(
    bot: discord.ext.commands.Bot,
    guild_id: str,
    batch_id: str,
    batch_data: dict,
    store: TriviaRedisStore
) -> None:
    """Update the batch question message with current answer statistics.

    Args:
        bot: The Discord bot instance
        guild_id: Guild ID as string
        batch_id: Batch game ID
        batch_data: Batch data containing message_id and channel_id
        store: TriviaRedisStore instance
    """
    # Import here to avoid circular dependency
    from bot.app.commands.trivia.trivia import create_batch_question_embed

    message_id = batch_data.get("message_id")
    channel_id = batch_data.get("channel_id")

    if not message_id or not channel_id:
        logger.warning(f"Missing message_id or channel_id for batch {batch_id[:8]}")
        return

    # Get the channel and message
    channel = bot.get_channel(channel_id)
    if not channel:
        logger.warning(f"Could not find channel {channel_id}")
        return

    try:
        message = await channel.fetch_message(message_id)
    except discord.NotFound:
        logger.warning(f"Could not find message {message_id}")
        return
    except discord.Forbidden:
        logger.warning(f"No permission to fetch message {message_id}")
        return

    # Get all submissions and questions
    submissions = await store.get_batch_submissions(guild_id, batch_id)
    questions = await store.get_batch_questions(guild_id, batch_id)

    # Count stats per question
    stats = count_batch_submissions(submissions, len(questions))

    # Recreate the embed with stats
    ends_at = dt.datetime.fromisoformat(batch_data["ends_at"])

    # Convert questions dict to list for embed creation
    questions_list = [questions[str(i)] for i in range(1, len(questions) + 1)]

    updated_embed = create_batch_question_embed(
        questions=questions_list,
        batch_id=batch_id,
        category=batch_data.get("category", "General"),
        ends_at=ends_at,
        stats=stats
    )

    # Update the message
    try:
        await message.edit(embed=updated_embed)
        logger.info(f"Updated batch question stats for game {batch_id[:8]}")
    except discord.Forbidden:
        logger.warning(f"No permission to edit message {message_id}")
    except Exception as e:
        logger.error(f"Failed to edit message: {e}")


def count_batch_submissions(
    submissions: dict,
    question_count: int
) -> dict:
    """Count correct/incorrect per question.

    Args:
        submissions: Dictionary of user_id -> submission_data
        question_count: Total number of questions in batch

    Returns:
        {
            "1": {"correct": 5, "incorrect": 2},
            "2": {"correct": 3, "incorrect": 4},
            "3": {"correct": 1, "incorrect": 6}
        }
    """
    stats = {}

    # Initialize stats for all questions
    for i in range(1, question_count + 1):
        stats[str(i)] = {"correct": 0, "incorrect": 0}

    # Count answers
    for user_id, submission_data in submissions.items():
        answers = submission_data.get("answers", {})

        for q_num, answer_data in answers.items():
            if q_num in stats:
                if answer_data.get("is_correct"):
                    stats[q_num]["correct"] += 1
                else:
                    stats[q_num]["incorrect"] += 1

    return stats


async def submit_batch_trivia_answer(
    bot: discord.ext.commands.Bot,
    interaction: discord.Interaction,
    answer_text: str,
    guild_id: str,
    batch_id: str = None
) -> None:
    """Submit answers to a batch trivia game.

    Flow:
    1. Find active batch game (by batch_id or thread_id)
    2. Parse multi-line answers → dict
    3. Load questions and answer_maps from Redis
    4. Validate each answer individually
    5. Submit atomically via Lua script
    6. Send formatted feedback to user
    7. Update message embed with stats

    Args:
        bot: The Discord bot instance
        interaction: The Discord interaction (should already be deferred)
        answer_text: The user's multi-line answers
        guild_id: The guild ID as a string
        batch_id: Optional batch ID to submit to (if known)
    """
    store = TriviaRedisStore()

    # Find active game for this channel
    active_games = await store.get_active_games(guild_id)

    batch_data = None

    # If batch_id is provided, use it directly
    if batch_id:
        batch_data = active_games.get(batch_id)
        if not batch_data:
            await interaction.followup.send(
                "❌ This trivia game is no longer active.", ephemeral=True
            )
            return
    else:
        # Find game by thread_id or channel_id
        channel_id = interaction.channel.id

        for gid, gdata in active_games.items():
            if gdata.get("thread_id") == channel_id or gdata.get("channel_id") == channel_id:
                batch_id = gid
                batch_data = gdata
                break

    if not batch_id or not batch_data:
        await interaction.followup.send(
            "❌ No active trivia game found for this channel.", ephemeral=True
        )
        return

    # Parse multi-line answers
    parsed_answers = parse_batch_answers(answer_text)

    if not parsed_answers:
        await interaction.followup.send(
            "❌ Could not parse your answers. Please format them as:\n"
            "```\n1. your answer\n2. your answer\n3. your answer\n```",
            ephemeral=True
        )
        return

    # Load questions and answer maps
    questions = await store.get_batch_questions(guild_id, batch_id)

    if not questions:
        await interaction.followup.send(
            "❌ Could not load questions for this game.", ephemeral=True
        )
        return

    # Build answer_maps dict for each question
    answer_maps = {}
    for q_num, q_data in questions.items():
        answer_maps[q_num] = q_data.get("answer_map", {})

    # Validate all answers
    try:
        validated_answers = await validate_batch_answers(parsed_answers, questions, answer_maps)
    except Exception as e:
        logger.error(f"Failed to validate batch answers: {e}")
        await interaction.followup.send(
            "❌ Failed to validate your answers. Please try again.", ephemeral=True
        )
        return

    # Calculate score
    correct_count = sum(1 for v in validated_answers.values() if v.get("is_correct", False))
    total_count = len(questions)
    score = f"{correct_count}/{total_count}"

    # Prepare submission data
    user_id_str = str(interaction.user.id)
    submission_data = {
        "answers": validated_answers,
        "submitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "score": score
    }

    # Submit atomically using Lua script
    result = await store.submit_batch_answer_atomic(
        guild_id, batch_id, user_id_str, submission_data
    )

    # Handle result
    if result.get("err"):
        error_code = result["err"]

        if error_code == "GAME_NOT_FOUND":
            await interaction.followup.send(
                "❌ This trivia game is no longer active.", ephemeral=True
            )
        elif error_code == "GAME_CLOSED":
            await interaction.followup.send(
                "❌ This game has already been closed.", ephemeral=True
            )
        elif error_code == "WINDOW_CLOSED":
            await interaction.followup.send(
                "❌ The answer window has closed. Wait for results!", ephemeral=True
            )
        elif error_code == "ALREADY_SUBMITTED":
            await interaction.followup.send(
                "❌ You have already submitted answers to this trivia game.", ephemeral=True
            )
        else:
            await interaction.followup.send(
                "❌ Failed to submit answers. Please try again.", ephemeral=True
            )
        return

    # Success! Send formatted feedback
    feedback_message = format_batch_feedback(validated_answers, questions)
    await interaction.followup.send(feedback_message, ephemeral=True)

    # Update the question message with latest stats
    try:
        await update_batch_question_stats(bot, guild_id, batch_id, batch_data, store)
    except Exception as e:
        logger.warning(f"Failed to update batch question stats: {e}")
