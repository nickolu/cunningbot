"""Redis storage layer for GitHub issue filing.

Key schema:
    github:{guild_id}:issues_this_hour   # int, TTL 3600 -- rate limit counter
"""

import logging

from bot.app.redis.client import get_redis_client

logger = logging.getLogger("GitHubRedisStore")

#: Issues one guild may file per hour. A public channel plus a language model is
#: a way to fill a repo with junk faster than anyone can close it.
ISSUES_PER_HOUR = 5

_WINDOW_SECONDS = 3600


class GitHubRedisStore:
    """Rate-limit bookkeeping for the create_github_issue tool."""

    KEY_PREFIX = "github"

    def __init__(self):
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    def _key(self, guild_id: str) -> str:
        return "%s:%s:issues_this_hour" % (self.KEY_PREFIX, guild_id)

    async def claim_issue_slot(self, guild_id: str) -> bool:
        """Consume one of this guild's hourly slots. False when exhausted.

        Counts the attempt before it happens, so a failed GitHub call still
        spends a slot. That is the safe direction: a retry loop that spent no
        slots would defeat the limit entirely.
        """
        key = self._key(guild_id)
        try:
            count = await self.redis.incr(key)
            if count == 1:
                await self.redis.expire(key, _WINDOW_SECONDS)
            return bool(count <= ISSUES_PER_HOUR)
        except Exception as e:  # noqa: BLE001 - Redis down must not be a bypass
            logger.error("Rate limit check failed for guild %s: %s", guild_id, e)
            return False

    async def slots_remaining(self, guild_id: str) -> int:
        try:
            raw = await self.redis.get(self._key(guild_id))
            used = int(raw) if raw else 0
        except (ValueError, TypeError):
            used = 0
        except Exception as e:  # noqa: BLE001
            logger.error("Rate limit read failed for guild %s: %s", guild_id, e)
            return 0
        return max(0, ISSUES_PER_HOUR - used)
