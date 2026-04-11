import asyncio

import aiohttp
import feedparser

FEED_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
FEED_FETCH_HEADERS = {
    "User-Agent": FEED_USER_AGENT,
    "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    "Accept-Language": "en-US,en;q=0.9",
}


async def fetch_and_parse_feed(feed_url: str, timeout: float = 30.0) -> feedparser.FeedParserDict:
    """Fetch a feed via aiohttp and parse the bytes with feedparser.

    Feedparser's built-in HTTP client sends headers (notably ``A-IM: feed``)
    that Reddit and some other hosts block with a 403. Fetching the bytes
    ourselves with a browser-style User-Agent avoids the block.
    """
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(headers=FEED_FETCH_HEADERS, timeout=client_timeout) as session:
        async with session.get(feed_url) as response:
            response.raise_for_status()
            body = await response.read()
    return await asyncio.to_thread(feedparser.parse, body)
