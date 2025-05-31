
from typing import List, Optional, Any, Callable
import functools
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CunningBot")

def split_message(text: str, max_length: int = 2000) -> List[str]:
    # Split at the last newline before max_length, or hard split if none
    chunks = []
    while len(text) > max_length:
        split_at = text.rfind('\n', 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:]
    if text:
        chunks.append(text)
    return chunks

def concat_url_params(**kwargs: Optional[str]) -> str:
    return "&".join([f"{key}={value}" for key, value in kwargs.items()])

def logging_decorator(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to log method name and return value."""
    code = getattr(func, "__code__", None)
    is_coroutine = bool(code and (code.co_flags & 0x80))

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.info(f"Calling {func.__qualname__}")
        result = func(*args, **kwargs)
        logger.info(f"{func.__qualname__} returned: {result!r}")
        return result

    @functools.wraps(func)
    async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
        logger.info(f"Calling {func.__qualname__}")
        result = await func(*args, **kwargs)
        logger.info(f"{func.__qualname__} returned: {result!r}")
        return result

    return async_wrapper if is_coroutine else sync_wrapper
