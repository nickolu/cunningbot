"""
OpenTDB API client for fetching trivia questions.

https://opentdb.com/api.php
"""

import asyncio
import html
import logging
from typing import Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

BASE_URL = "https://opentdb.com/api.php"


class OpenTDBClient:
    """HTTP client for OpenTDB API."""

    def __init__(self, timeout: int = 10, max_retries: int = 3):
        """
        Initialize the OpenTDB client.

        Args:
            timeout: Request timeout in seconds
            max_retries: Maximum number of retry attempts
        """
        self.timeout = timeout
        self.max_retries = max_retries

    async def fetch_questions(
        self,
        amount: int,
        category: int,
        difficulty: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        Fetch questions from OpenTDB API.

        Args:
            amount: Number of questions to fetch (1-50)
            category: Category ID (9-32)
            difficulty: Optional difficulty filter ("easy", "medium", "hard")

        Returns:
            List of question dictionaries with HTML-decoded content

        Raises:
            Exception: If API request fails after all retries
        """
        params = {
            "amount": amount,
            "category": category,
            "type": "multiple"
        }

        if difficulty:
            params["difficulty"] = difficulty

        for attempt in range(self.max_retries):
            try:
                timeout = aiohttp.ClientTimeout(total=self.timeout)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(BASE_URL, params=params) as response:
                        if response.status == 429:
                            # Rate limited - exponential backoff
                            wait_time = (2 ** attempt) * 1
                            logger.warning(f"OpenTDB rate limited, waiting {wait_time}s (attempt {attempt + 1}/{self.max_retries})")
                            await asyncio.sleep(wait_time)
                            continue

                        if response.status != 200:
                            logger.error(f"OpenTDB API returned status {response.status}")
                            raise Exception(f"OpenTDB API returned status {response.status}")

                        data = await response.json()

                        # Check response code
                        response_code = data.get("response_code")
                        if response_code != 0:
                            error_msg = self._get_error_message(response_code)
                            logger.error(f"OpenTDB API error: {error_msg} (code: {response_code})")
                            raise Exception(f"OpenTDB API error: {error_msg}")

                        # Parse and decode questions
                        results = data.get("results", [])
                        if not results:
                            logger.error("OpenTDB returned empty results")
                            raise Exception("OpenTDB returned empty results")

                        questions = []
                        for item in results:
                            decoded_question = html.unescape(item["question"])
                            decoded_answer = html.unescape(item["correct_answer"])
                            decoded_category = html.unescape(item["category"])

                            questions.append({
                                "question": decoded_question,
                                "correct_answer": decoded_answer,
                                "category": decoded_category,
                                "difficulty": item["difficulty"]
                            })

                        logger.info(f"Successfully fetched {len(questions)} questions from OpenTDB (category: {category}, difficulty: {difficulty})")

                        # Log first question as sample to verify HTML decoding
                        if questions:
                            sample = questions[0]
                            logger.info(f"Sample question (decoded): {sample['question'][:100]}")
                            logger.info(f"Sample answer (decoded): {sample['correct_answer']}")

                        return questions

            except asyncio.TimeoutError:
                logger.warning(f"OpenTDB request timeout (attempt {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                raise Exception("OpenTDB request timeout after all retries")

            except aiohttp.ClientError as e:
                logger.warning(f"OpenTDB network error: {e} (attempt {attempt + 1}/{self.max_retries})")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(1)
                    continue
                raise Exception(f"OpenTDB network error: {e}")

            except Exception as e:
                logger.error(f"OpenTDB error: {e}")
                raise

        raise Exception("OpenTDB request failed after all retries")

    @staticmethod
    def _get_error_message(response_code: int) -> str:
        """Get human-readable error message for OpenTDB response code."""
        error_messages = {
            1: "No results - not enough questions available",
            2: "Invalid parameter - check category/difficulty",
            3: "Token not found",
            4: "Token empty - all questions exhausted",
            5: "Rate limit exceeded"
        }
        return error_messages.get(response_code, f"Unknown error (code: {response_code})")
