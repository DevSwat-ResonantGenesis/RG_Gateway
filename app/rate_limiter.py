"""Rate limiting middleware for API Gateway.

Enhanced with per-user tier-based limits for production readiness.

Author: Resonant Genesis Team
Updated: December 29, 2025
"""

import time
from collections import defaultdict
from typing import Dict, Optional, Tuple
from enum import Enum

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import settings


class UserTier(Enum):
    """User subscription tiers with different rate limits."""
    DEVELOPER = "developer"
    PLUS = "plus"
    ENTERPRISE = "enterprise"
    # Legacy aliases
    FREE = "developer"
    PRO = "plus"


# Per-tier rate limits (requests per minute)
TIER_LIMITS = {
    UserTier.DEVELOPER: {"global": 30, "chat": 10, "memory": 30, "agent": 5},
    UserTier.PLUS: {"global": 100, "chat": 30, "memory": 100, "agent": 20},
    UserTier.ENTERPRISE: {"global": 1000, "chat": 200, "memory": 1000, "agent": 200},
}


class RateLimiter:
    """Enhanced rate limiter with per-endpoint limits and burst allowance."""

    def __init__(self):
        # {client_id: [(timestamp, count), ...]}
        self.requests: Dict[str, list] = defaultdict(list)
        # {client_id:endpoint: [(timestamp, count), ...]}
        self.endpoint_requests: Dict[str, list] = defaultdict(list)
        
        self.window_seconds = 60
        self.max_requests = getattr(settings, "RATE_LIMIT_PER_MINUTE", 1000)
        self.burst_allowance = 50  # Extra requests allowed in burst
        
        # Per-endpoint rate limits (requests per minute)
        self.endpoint_limits = {
            "/billing": 100,
            "/finance": 100,
            "/agents": 200,
            "/resonant-chat/message": 60,
            "/code/execute": 30,
            "/ml/training-jobs": 20,
            "/admin": 50,
        }
        
        # Paths exempt from rate limiting
        self.exempt_paths = {
            "/health", "/metrics", "/", "/docs", "/openapi.json", "/redoc",
        }
        self.exempt_prefixes = (
            "/api/auth/", "/auth/", "/public/",
            # Public read-only dashboard endpoints
            "/policies", "/ml/predictions", "/audit/ai-audit",
        )

    def _clean_old_requests(self, client_id: str, now: float):
        """Remove requests outside the current window."""
        cutoff = now - self.window_seconds
        self.requests[client_id] = [
            (ts, count) for ts, count in self.requests[client_id]
            if ts > cutoff
        ]

    def _clean_endpoint_requests(self, key: str, now: float):
        """Remove endpoint requests outside the current window."""
        cutoff = now - self.window_seconds
        self.endpoint_requests[key] = [
            (ts, count) for ts, count in self.endpoint_requests[key]
            if ts > cutoff
        ]

    def _get_endpoint_limit(self, path: str) -> Optional[int]:
        """Get rate limit for a specific endpoint."""
        for prefix, limit in self.endpoint_limits.items():
            if path.startswith(prefix):
                return limit
        return None

    def is_allowed(self, client_id: str, path: str = "") -> Tuple[bool, int, int, Optional[int]]:
        """Check if request is allowed.
        
        Returns (allowed, remaining, reset_seconds, endpoint_remaining).
        """
        now = time.time()
        self._clean_old_requests(client_id, now)

        # Count requests in current window
        total = sum(count for _, count in self.requests[client_id])

        # Check global limit (with burst allowance)
        effective_limit = self.max_requests + self.burst_allowance
        if total >= effective_limit:
            if self.requests[client_id]:
                oldest = min(ts for ts, _ in self.requests[client_id])
                reset = int(oldest + self.window_seconds - now)
            else:
                reset = self.window_seconds
            return False, 0, max(1, reset), None

        # Check per-endpoint limit
        endpoint_limit = self._get_endpoint_limit(path)
        endpoint_remaining = None
        
        if endpoint_limit:
            endpoint_key = f"{client_id}:{path.split('/')[1]}"  # e.g., "user:123:/billing"
            self._clean_endpoint_requests(endpoint_key, now)
            endpoint_total = sum(count for _, count in self.endpoint_requests[endpoint_key])
            
            if endpoint_total >= endpoint_limit:
                if self.endpoint_requests[endpoint_key]:
                    oldest = min(ts for ts, _ in self.endpoint_requests[endpoint_key])
                    reset = int(oldest + self.window_seconds - now)
                else:
                    reset = self.window_seconds
                return False, 0, max(1, reset), 0
            
            self.endpoint_requests[endpoint_key].append((now, 1))
            endpoint_remaining = endpoint_limit - endpoint_total - 1

        # Add this request to global counter
        self.requests[client_id].append((now, 1))
        remaining = self.max_requests - total - 1

        return True, remaining, self.window_seconds, endpoint_remaining


rate_limiter = RateLimiter()


def add_cors_headers(response: Response, request: Request) -> Response:
    """Add CORS headers to response."""
    origin = request.headers.get("origin", "*")
    response.headers["Access-Control-Allow-Origin"] = origin
    response.headers["Access-Control-Allow-Credentials"] = "true"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
    response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Requested-With, Accept, Origin, X-CSRF-Token"
    return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        
        # Skip rate limiting for exempt paths
        if path in rate_limiter.exempt_paths:
            return await call_next(request)
        if any(path.startswith(p) for p in rate_limiter.exempt_prefixes):
            return await call_next(request)
        
        # Skip OPTIONS requests (CORS preflight)
        if request.method == "OPTIONS":
            return await call_next(request)
        
        # Get client identifier
        client_id = self._get_client_id(request)

        # Check rate limit (with path for per-endpoint limits)
        allowed, remaining, reset, endpoint_remaining = rate_limiter.is_allowed(client_id, path)

        if not allowed:
            response = Response(
                status_code=429,
                content=b'{"detail":"Rate limit exceeded. Please try again later."}',
                media_type="application/json",
                headers={
                    "X-RateLimit-Limit": str(rate_limiter.max_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset),
                    "Retry-After": str(reset),
                },
            )
            return add_cors_headers(response, request)

        # Process request
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(rate_limiter.max_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset)
        
        # Add endpoint-specific rate limit header if applicable
        if endpoint_remaining is not None:
            response.headers["X-RateLimit-Endpoint-Remaining"] = str(endpoint_remaining)

        return response

    def _get_client_id(self, request: Request) -> str:
        """Get client identifier for rate limiting."""
        # Try to get user ID from header (set by auth middleware)
        user_id = request.headers.get("x-user-id")
        if user_id:
            return f"user:{user_id}"

        # Fall back to IP address
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        else:
            ip = request.client.host if request.client else "unknown"

        return f"ip:{ip}"
    
    def _get_user_tier(self, request: Request) -> UserTier:
        """Get user's subscription tier from request headers."""
        tier_header = request.headers.get("x-user-tier", "free").lower()
        try:
            return UserTier(tier_header)
        except ValueError:
            return UserTier.FREE
    
    def get_tier_limit(self, tier: UserTier, action: str = "global") -> int:
        """Get rate limit for a specific tier and action."""
        tier_config = TIER_LIMITS.get(tier, TIER_LIMITS[UserTier.FREE])
        return tier_config.get(action, tier_config["global"])
