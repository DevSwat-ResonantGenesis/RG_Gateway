"""Idempotency Key middleware for preventing duplicate operations.

Supports POST/PUT/PATCH operations on critical endpoints like billing and finance.
"""

import hashlib
import json
import time
from typing import Dict, Optional, Tuple
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class IdempotencyStore:
    """In-memory idempotency key store with TTL."""
    
    def __init__(self, ttl_seconds: int = 86400):  # 24 hours default
        self.store: Dict[str, Tuple[float, int, bytes, str]] = {}  # key -> (timestamp, status_code, body, content_type)
        self.ttl = ttl_seconds
        
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
    
    def _cleanup_expired(self):
        """Remove expired entries."""
        now = time.time()
        expired = [k for k, (ts, _, _, _) in self.store.items() if now - ts > self.ttl]
        for k in expired:
            del self.store[k]
    
    def get(self, key: str) -> Optional[Tuple[int, bytes, str]]:
        """Get cached response for idempotency key."""
        self._cleanup_expired()
        
        if key in self.store:
            ts, status_code, body, content_type = self.store[key]
            if time.time() - ts <= self.ttl:
                return status_code, body, content_type
            else:
                del self.store[key]
        return None
    
    def set(self, key: str, status_code: int, body: bytes, content_type: str):
        """Store response for idempotency key."""
        self.store[key] = (time.time(), status_code, body, content_type)
    
    def requires_idempotency(self, path: str, method: str) -> bool:
        """Check if endpoint requires idempotency key."""
        if method not in self.idempotent_methods:
            return False
        return any(path.startswith(prefix) for prefix in self.required_prefixes)


idempotency_store = IdempotencyStore()


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Middleware to handle idempotency keys."""
    
    HEADER_NAME = "Idempotency-Key"
    
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        method = request.method.upper()
        
        # Skip if not an idempotent endpoint
        if not idempotency_store.requires_idempotency(path, method):
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
        cached = idempotency_store.get(key_hash)
        if cached:
            status_code, body, content_type = cached
            return Response(
                status_code=status_code,
                content=body,
                media_type=content_type,
                headers={
                    "X-Idempotency-Replayed": "true",
                    "X-Idempotency-Key": idempotency_key,
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
            idempotency_store.set(key_hash, response.status_code, body, content_type)
            
            # Return new response with body
            return Response(
                status_code=response.status_code,
                content=body,
                media_type=content_type,
                headers={
                    **dict(response.headers),
                    "X-Idempotency-Key": idempotency_key,
                }
            )
        
        return response
