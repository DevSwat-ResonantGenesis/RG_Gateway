"""Redis failure mode handler for graceful degradation.

Author: Resonant Genesis Team
Updated: January 11, 2026
"""

import asyncio
import time
from typing import Optional, Dict, Any
from dataclasses import dataclass
import redis.asyncio as redis
from redis.asyncio import Redis


@dataclass
class RedisFailureConfig:
    """Configuration for Redis failure handling."""
    max_retries: int = 3
    retry_delay: float = 1.0
    circuit_breaker_timeout: float = 30.0
    fallback_cache_ttl: int = 300  # 5 minutes


class RedisFailureHandler:
    """Handles Redis failures with circuit breaker pattern."""
    
    def __init__(self):
        self.redis: Optional[Redis] = None
        self.is_circuit_open = False
        self.circuit_open_time = 0
        self.failure_count = 0
        self.last_failure_time = 0
        self.config = RedisFailureConfig()
        
        # In-memory fallback cache for when Redis is down
        self.fallback_cache: Dict[str, Any] = {}
        self.fallback_cache_timestamps: Dict[str, float] = {}
        
    async def connect(self, redis_url: str = "redis://redis:6379"):
        """Connect to Redis with retry logic."""
        if self.redis:
            return
            
        for attempt in range(self.config.max_retries):
            try:
                self.redis = Redis.from_url(redis_url, decode_responses=True)
                await self.redis.ping()
                # Reset failure state on successful connection
                self.is_circuit_open = False
                self.failure_count = 0
                return
            except Exception as e:
                self.failure_count += 1
                self.last_failure_time = time.time()
                
                if attempt < self.config.max_retries - 1:
                    await asyncio.sleep(self.config.retry_delay * (2 ** attempt))
                else:
                    # Open circuit breaker
                    self.is_circuit_open = True
                    self.circuit_open_time = time.time()
                    raise ConnectionError(f"Redis connection failed after {self.config.max_retries} attempts: {e}")
    
    async def disconnect(self):
        """Close Redis connection."""
        if self.redis:
            await self.redis.close()
            self.redis = None
    
    async def check_circuit_breaker(self) -> bool:
        """Check if circuit breaker should be reset."""
        if not self.is_circuit_open:
            return True
            
        # Reset circuit breaker after timeout
        if time.time() - self.circuit_open_time > self.config.circuit_breaker_timeout:
            self.is_circuit_open = False
            self.failure_count = 0
            return True
            
        return False
    
    async def execute_with_fallback(self, operation, *args, **kwargs):
        """Execute Redis operation with fallback to in-memory cache."""
        # Check circuit breaker
        if not await self.check_circuit_breaker():
            # Circuit is open, try fallback for read operations
            if operation.__name__ in ['get', 'hget', 'lrange', 'smembers']:
                return await self._fallback_get(operation.__name__, *args, **kwargs)
            raise ConnectionError("Circuit breaker is open")
        
        # Try Redis operation
        try:
            if not self.redis:
                await self.connect()
            
            result = await operation(self.redis, *args, **kwargs)
            return result
            
        except Exception as e:
            # Increment failure count
            self.failure_count += 1
            self.last_failure_time = time.time()
            
            # Open circuit breaker if too many failures
            if self.failure_count >= self.config.max_retries:
                self.is_circuit_open = True
                self.circuit_open_time = time.time()
            
            # Fall back to in-memory cache for read operations
            if operation.__name__ in ['get', 'hget', 'lrange', 'smembers']:
                return await self._fallback_get(operation.__name__, *args, **kwargs)
            
            # For write operations, we can't fallback
            raise ConnectionError(f"Redis operation failed and no fallback available: {e}")
    
    async def _fallback_get(self, operation: str, *args, **kwargs):
        """Fallback get operation using in-memory cache."""
        cache_key = f"{operation}:{args[0]}" if args else operation
        
        # Check if cache entry exists and is not expired
        if cache_key in self.fallback_cache_timestamps:
            age = time.time() - self.fallback_cache_timestamps[cache_key]
            if age < self.config.fallback_cache_ttl:
                return self.fallback_cache.get(cache_key)
            else:
                # Expired, remove it
                self.fallback_cache.pop(cache_key, None)
                self.fallback_cache_timestamps.pop(cache_key, None)
        
        return None
    
    async def set_fallback(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set value in fallback cache."""
        self.fallback_cache[key] = value
        self.fallback_cache_timestamps[key] = time.time()
        
        # Schedule cleanup if TTL provided
        if ttl:
            asyncio.create_task(self._cleanup_fallback(key, ttl))
    
    async def _cleanup_fallback(self, key: str, ttl: int):
        """Clean up fallback cache entry after TTL."""
        await asyncio.sleep(ttl)
        self.fallback_cache.pop(key, None)
        self.fallback_cache_timestamps.pop(key, None)
    
    def get_status(self) -> Dict[str, Any]:
        """Get current status of Redis connection."""
        return {
            "redis_connected": self.redis is not None,
            "circuit_breaker_open": self.is_circuit_open,
            "failure_count": self.failure_count,
            "last_failure_time": self.last_failure_time,
            "fallback_cache_size": len(self.fallback_cache),
            "circuit_open_time": self.circuit_open_time if self.is_circuit_open else None
        }


# Global Redis failure handler instance
redis_failure_handler = RedisFailureHandler()
