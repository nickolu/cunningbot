"""Custom exceptions for Redis operations."""


class RedisOperationError(Exception):
    """Base exception for Redis operations."""
    pass


class LockAcquisitionError(RedisOperationError):
    """Failed to acquire distributed lock."""
    pass


class RetryableRedisError(RedisOperationError):
    """Redis error that can be retried."""
    pass
