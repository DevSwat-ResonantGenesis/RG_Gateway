"""Redis-backed Idempotency Key Store.

Provides distributed idempotency key storage using Redis.
Falls back to in-memory storage if Redis is unavailable.
"""

import os
import json
import hashlib
import time
from typing import Dict, Optional, Tuple

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

# Try to import redis
try:
    import redis.asyncio as redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False


# Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
IDEMPOTENCY_TTL = int(os.getenv("IDEMPOTENCY_TTL", 86400))  # 24 hours


class RedisIdempotencyStore:
    """Redis-backed idempotency store with in-memory fallback."""
    
    def __init__(self, ttl_seconds: int = IDEMPOTENCY_TTL):
        self.ttl = ttl_seconds
        self._redis_client: Optional[redis.Redis] = None
        self._redis_available = False
        
        # In-memory fallback
        self._memory_store: Dict[str, Tuple[float, int, bytes, str]] = {}
        
        # Endpoints that require idempotency
        self.required_prefixes = (
            "/billing",
            "/finance",
            "/agents",
            "/users",
            "/orgs",
        )
        
        # Methods that support idempotency
        self.idempotent_methods = {"POST", "PUT", "PATCH"}
    
    async def _get_redis(self) -> Optional[redis.Redis]:
        """Get Redis client, creating if needed."""
        if not REDIS_AVAILABLE:
            return None
        
        if self._redis_client is None:
            try:
                self._redis_client = redis.from_url(
                    REDIS_URL,
                    encoding="utf-8",
                    decode_responses=False,
                )
                # Test connection
                await self._redis_client.ping()
                self._redis_available = True
            except Exception:
                self._redis_available = False
                self._redis_client = None
        
        return self._redis_client if self._redis_available else None
    
    def _cleanup_memory(self):
        """Remove expired entries from memory store."""
        now = time.time()
        expired = [k for k, (ts, _, _, _) in self._memory_store.items() if now - ts > self.ttl]
        for k in expired:
            del self._memory_store[k]
    
    async def get(self, key: str) -> Optional[Tuple[int, bytes, str]]:
        """Get cached response for idempotency key."""
        redis_client = await self._get_redis()
        
        if redis_client:
            try:
                data = await redis_client.get(f"idempotency:{key}")
                if data:
                    parsed = json.loads(data)
                    return (
                        parsed["status_code"],
                        parsed["body"].encode() if isinstance(parsed["body"], str) else parsed["body"],
                        parsed["content_type"],
                    )
            except Exception:
                pass
        
        # Fallback to memory
        self._cleanup_memory()
        if key in self._memory_store:
            ts, status_code, body, content_type = self._memory_store[key]
            if time.time() - ts <= self.ttl:
                return status_code, body, content_type
            else:
                del self._memory_store[key]
        
        return None
    
    async def set(self, key: str, status_code: int, body: bytes, content_type: str):
        """Store response for idempotency key."""
        redis_client = await self._get_redis()
        
        if redis_client:
            try:
                data = json.dumps({
                    "status_code": status_code,
                    "body": body.decode() if isinstance(body, bytes) else body,
                    "content_type": content_type,
                    "timestamp": time.time(),
                })
                await redis_client.setex(f"idempotency:{key}", self.ttl, data)
                return
            except Exception:
                pass
        
        # Fallback to memory
        self._memory_store[key] = (time.time(), status_code, body, content_type)
    
    def requires_idempotency(self, path: str, method: str) -> bool:
        """Check if endpoint requires idempotency key."""
        if method not in self.idempotent_methods:
            return False
        return any(path.startswith(prefix) for prefix in self.required_prefixes)
    
    async def get_stats(self) -> Dict:
        """Get idempotency store statistics."""
        redis_client = await self._get_redis()
        
        stats = {
            "redis_available": self._redis_available,
            "memory_entries": len(self._memory_store),
            "ttl_seconds": self.ttl,
        }
        
        if redis_client:
            try:
                keys = await redis_client.keys("idempotency:*")
                stats["redis_entries"] = len(keys)
            except Exception:
                stats["redis_entries"] = 0
        
        return stats


# Global instance
redis_idempotency_store = RedisIdempotencyStore()


class RedisIdempotencyMiddleware(BaseHTTPMiddleware):
    """Middleware to handle idempotency keys with Redis backing."""
    
    HEADER_NAME = "Idempotency-Key"
    
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        method = request.method.upper()
        
        # Skip if not an idempotent endpoint
        if not redis_idempotency_store.requires_idempotency(path, method):
            return await call_next(request)
        
        # Get idempotency key from header
        idempotency_key = request.headers.get(self.HEADER_NAME)
        
        if not idempotency_key:
            # No key provided - process normally but warn
            response = await call_next(request)
            response.headers["X-Idempotency-Warning"] = "No Idempotency-Key provided for mutating operation"
            return response
        
        # Create composite key with user context
        user_id = request.headers.get("x-user-id", "anonymous")
        composite_key = f"{user_id}:{path}:{idempotency_key}"
        key_hash = hashlib.sha256(composite_key.encode()).hexdigest()
        
        # Check if we have a cached response
        cached = await redis_idempotency_store.get(key_hash)
        if cached:
            status_code, body, content_type = cached
            return Response(
                status_code=status_code,
                content=body,
                media_type=content_type,
                headers={
                    "X-Idempotency-Replayed": "true",
                    "X-Idempotency-Key": idempotency_key,
                    "X-Idempotency-Store": "redis" if redis_idempotency_store._redis_available else "memory",
                }
            )
        
        # Process the request
        response = await call_next(request)
        
        # Cache the response if successful (2xx or 4xx client errors)
        if 200 <= response.status_code < 500:
            # Read response body
            body = b""
            async for chunk in response.body_iterator:
                body += chunk
            
            content_type = response.headers.get("content-type", "application/json")
            
            # Store for future replay
            await redis_idempotency_store.set(key_hash, response.status_code, body, content_type)
            
            # Return new response with body
            return Response(
                status_code=response.status_code,
                content=body,
                media_type=content_type,
                headers={
                    **dict(response.headers),
                    "X-Idempotency-Key": idempotency_key,
                    "X-Idempotency-Store": "redis" if redis_idempotency_store._redis_available else "memory",
                }
            )
        
        return response
