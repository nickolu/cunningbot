"""Redis storage for the agent's suggested-reply buttons.

A channel has at most one live set of suggestions: the buttons under the most
recent agent reply there. Posting the next reply replaces the record, which is
what expires the old buttons.

Key schema:
    suggest:{guild_id}:{channel_id}   # Hash: nonce, message_id, options, created_at

`nonce` is also in every button's custom_id (`suggest:{nonce}:{index}`), so a
click on buttons that are no longer live fails to match and is refused.
`options` is a JSON list of the button texts, in button order.

`claim` compares the nonce and deletes the record in one Lua call, so of two
people clicking at the same moment exactly one gets the options.

Records expire after `SUGGESTION_TTL_SECONDS`, so a channel the bot never
speaks in again doesn't keep a live set forever.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bot.app.redis.client import get_redis_client
from bot.app.redis.serialization import channel_id_to_str, guild_id_to_str
from bot.app.utils.logger import get_logger

logger = get_logger()

SUGGESTION_TTL_SECONDS = 7 * 24 * 60 * 60

_CLAIM_SCRIPT = """
if redis.call('HGET', KEYS[1], 'nonce') == ARGV[1] then
    local options = redis.call('HGET', KEYS[1], 'options')
    redis.call('DEL', KEYS[1])
    return options
end
return false
"""


def _parse_options(data: Optional[str]) -> Optional[List[str]]:
    if not data:
        return None
    try:
        options = json.loads(data)
    except json.JSONDecodeError as e:
        logger.error(f"Bad suggestion options JSON: {e}")
        return None
    if not isinstance(options, list):
        return None
    return [str(o) for o in options]


class SuggestionRedisStore:
    def __init__(self) -> None:
        self.redis_client = get_redis_client()
        self.redis = self.redis_client.redis

    def _key(self, guild_id: Any, channel_id: Any) -> str:
        return f"suggest:{guild_id_to_str(guild_id)}:{channel_id_to_str(channel_id)}"

    async def get(self, guild_id: Any, channel_id: Any) -> Optional[Dict[str, Any]]:
        """The channel's live suggestions, or None."""
        record = await self.redis.hgetall(self._key(guild_id, channel_id))
        if not record or not record.get("nonce"):
            return None
        return {
            "nonce": record["nonce"],
            "message_id": int(record["message_id"]) if record.get("message_id") else None,
            "options": _parse_options(record.get("options")) or [],
            "created_at": record.get("created_at"),
        }

    async def save(
        self,
        guild_id: Any,
        channel_id: Any,
        nonce: str,
        message_id: int,
        options: List[str],
    ) -> None:
        """Make these the channel's live suggestions, replacing any before."""
        key = self._key(guild_id, channel_id)
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.delete(key)
            pipe.hset(key, mapping={
                "nonce": nonce,
                "message_id": str(message_id),
                "options": json.dumps(options),
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
            pipe.expire(key, SUGGESTION_TTL_SECONDS)
            await pipe.execute()

    async def clear(self, guild_id: Any, channel_id: Any) -> None:
        await self.redis.delete(self._key(guild_id, channel_id))

    async def claim(self, guild_id: Any, channel_id: Any, nonce: str) -> Optional[List[str]]:
        """Take the options if `nonce` is still live, ending the set. Else None."""
        data = await self.redis.eval(
            _CLAIM_SCRIPT, 1, self._key(guild_id, channel_id), nonce
        )
        return _parse_options(data)
