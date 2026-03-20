"""API quotas and usage tracking per user."""

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response


class PlanTier(str, Enum):
    DEVELOPER = "developer"
    PLUS = "plus"
    ENTERPRISE = "enterprise"
    # Legacy mappings (for backward compatibility)
    FREE = "developer"
    STARTER = "developer"
    PRO = "plus"


@dataclass
class QuotaLimits:
    """Quota limits per plan tier."""
    requests_per_day: int
    requests_per_minute: int
    tokens_per_day: int
    agent_runs_per_day: int
    storage_mb: int
    concurrent_sessions: int


# Plan tier limits
PLAN_LIMITS: Dict[PlanTier, QuotaLimits] = {
    PlanTier.DEVELOPER: QuotaLimits(
        requests_per_day=1000,
        requests_per_minute=20,
        tokens_per_day=100000,
        agent_runs_per_day=10,
        storage_mb=100,
        concurrent_sessions=3,
    ),
    PlanTier.PLUS: QuotaLimits(
        requests_per_day=100000,
        requests_per_minute=200,
        tokens_per_day=5000000,
        agent_runs_per_day=1000,
        storage_mb=5000,
        concurrent_sessions=20,
    ),
    PlanTier.ENTERPRISE: QuotaLimits(
        requests_per_day=1000000,
        requests_per_minute=1000,
        tokens_per_day=50000000,
        agent_runs_per_day=10000,
        storage_mb=100000,
        concurrent_sessions=100,
    ),
}


@dataclass
class UserUsage:
    """Track user usage."""
    requests_today: int = 0
    requests_this_minute: List[float] = field(default_factory=list)
    tokens_today: int = 0
    agent_runs_today: int = 0
    storage_used_mb: float = 0
    active_sessions: int = 0
    last_reset: datetime = field(default_factory=datetime.utcnow)
    minute_window_start: float = field(default_factory=time.time)


class QuotaManager:
    """Manages API quotas per user."""

    def __init__(self):
        self.usage: Dict[str, UserUsage] = defaultdict(UserUsage)
        self.user_plans: Dict[str, PlanTier] = {}  # In production, fetch from DB

    def get_user_plan(self, user_id: str) -> PlanTier:
        """Get user's plan tier."""
        return self.user_plans.get(user_id, PlanTier.DEVELOPER)

    def set_user_plan(self, user_id: str, plan: PlanTier):
        """Set user's plan tier."""
        self.user_plans[user_id] = plan

    def get_limits(self, user_id: str) -> QuotaLimits:
        """Get quota limits for user."""
        plan = self.get_user_plan(user_id)
        return PLAN_LIMITS[plan]

    def _reset_daily_if_needed(self, user_id: str):
        """Reset daily counters if new day."""
        usage = self.usage[user_id]
        now = datetime.utcnow()
        if usage.last_reset.date() < now.date():
            usage.requests_today = 0
            usage.tokens_today = 0
            usage.agent_runs_today = 0
            usage.last_reset = now

    def _clean_minute_window(self, user_id: str):
        """Clean requests outside minute window."""
        usage = self.usage[user_id]
        now = time.time()
        cutoff = now - 60
        usage.requests_this_minute = [
            ts for ts in usage.requests_this_minute if ts > cutoff
        ]

    def check_request_quota(self, user_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Check if user can make a request.
        
        Returns (allowed, reason, quota_info).
        """
        self._reset_daily_if_needed(user_id)
        self._clean_minute_window(user_id)

        limits = self.get_limits(user_id)
        usage = self.usage[user_id]

        quota_info = {
            "plan": self.get_user_plan(user_id).value,
            "requests_today": usage.requests_today,
            "requests_limit_day": limits.requests_per_day,
            "requests_this_minute": len(usage.requests_this_minute),
            "requests_limit_minute": limits.requests_per_minute,
        }

        # Check daily limit
        if usage.requests_today >= limits.requests_per_day:
            return False, "Daily request quota exceeded", quota_info

        # Check per-minute limit
        if len(usage.requests_this_minute) >= limits.requests_per_minute:
            return False, "Per-minute request quota exceeded", quota_info

        return True, "", quota_info

    def record_request(self, user_id: str):
        """Record a request for quota tracking."""
        self._reset_daily_if_needed(user_id)
        usage = self.usage[user_id]
        usage.requests_today += 1
        usage.requests_this_minute.append(time.time())

    def check_token_quota(self, user_id: str, tokens: int) -> Tuple[bool, str]:
        """Check if user can use tokens."""
        self._reset_daily_if_needed(user_id)
        limits = self.get_limits(user_id)
        usage = self.usage[user_id]

        if usage.tokens_today + tokens > limits.tokens_per_day:
            return False, f"Daily token quota exceeded ({usage.tokens_today}/{limits.tokens_per_day})"

        return True, ""

    def record_tokens(self, user_id: str, tokens: int):
        """Record token usage."""
        self._reset_daily_if_needed(user_id)
        self.usage[user_id].tokens_today += tokens

    def check_agent_run_quota(self, user_id: str) -> Tuple[bool, str]:
        """Check if user can run an agent."""
        self._reset_daily_if_needed(user_id)
        limits = self.get_limits(user_id)
        usage = self.usage[user_id]

        if usage.agent_runs_today >= limits.agent_runs_per_day:
            return False, f"Daily agent run quota exceeded ({usage.agent_runs_today}/{limits.agent_runs_per_day})"

        return True, ""

    def record_agent_run(self, user_id: str):
        """Record an agent run."""
        self._reset_daily_if_needed(user_id)
        self.usage[user_id].agent_runs_today += 1

    def check_concurrent_sessions(self, user_id: str) -> Tuple[bool, str]:
        """Check if user can start a new session."""
        limits = self.get_limits(user_id)
        usage = self.usage[user_id]

        if usage.active_sessions >= limits.concurrent_sessions:
            return False, f"Concurrent session limit reached ({usage.active_sessions}/{limits.concurrent_sessions})"

        return True, ""

    def start_session(self, user_id: str):
        """Record session start."""
        self.usage[user_id].active_sessions += 1

    def end_session(self, user_id: str):
        """Record session end."""
        usage = self.usage[user_id]
        if usage.active_sessions > 0:
            usage.active_sessions -= 1

    def get_usage_summary(self, user_id: str) -> Dict[str, Any]:
        """Get usage summary for user."""
        self._reset_daily_if_needed(user_id)
        self._clean_minute_window(user_id)

        limits = self.get_limits(user_id)
        usage = self.usage[user_id]

        return {
            "plan": self.get_user_plan(user_id).value,
            "requests": {
                "today": usage.requests_today,
                "limit_day": limits.requests_per_day,
                "remaining_day": max(0, limits.requests_per_day - usage.requests_today),
                "this_minute": len(usage.requests_this_minute),
                "limit_minute": limits.requests_per_minute,
            },
            "tokens": {
                "today": usage.tokens_today,
                "limit_day": limits.tokens_per_day,
                "remaining_day": max(0, limits.tokens_per_day - usage.tokens_today),
            },
            "agent_runs": {
                "today": usage.agent_runs_today,
                "limit_day": limits.agent_runs_per_day,
                "remaining_day": max(0, limits.agent_runs_per_day - usage.agent_runs_today),
            },
            "storage": {
                "used_mb": usage.storage_used_mb,
                "limit_mb": limits.storage_mb,
            },
            "sessions": {
                "active": usage.active_sessions,
                "limit": limits.concurrent_sessions,
            },
        }


quota_manager = QuotaManager()


class QuotaMiddleware(BaseHTTPMiddleware):
    """Middleware to enforce API quotas."""

    # Paths exempt from quota checking
    EXEMPT_PATHS = {"/", "/health", "/metrics", "/docs", "/openapi.json", "/redoc"}
    EXEMPT_PREFIXES = (
        "/api/auth",
        # Public read-only dashboard endpoints
        "/policies",
        "/ml/predictions",
        "/audit/ai-audit",
    )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Skip quota check for exempt paths
        if path in self.EXEMPT_PATHS or any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        # Skip for health endpoints
        if path.endswith("/health"):
            return await call_next(request)

        # Get user ID
        user_id = request.headers.get("x-user-id")
        if not user_id:
            # Anonymous users get IP-based quota
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                user_id = f"anon:{forwarded.split(',')[0].strip()}"
            else:
                user_id = f"anon:{request.client.host if request.client else 'unknown'}"

        # Users with unlimited_credits get enterprise-level quotas
        unlimited_credits = (request.headers.get("x-unlimited-credits") or "").strip().lower() in ("true", "1", "yes")
        is_superuser = (request.headers.get("x-is-superuser") or "").strip().lower() in ("true", "1", "yes")
        if unlimited_credits or is_superuser:
            quota_manager.set_user_plan(user_id, PlanTier.ENTERPRISE)

        # Check quota
        allowed, reason, quota_info = quota_manager.check_request_quota(user_id)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "quota_exceeded",
                    "message": reason,
                    "quota": quota_info,
                },
                headers={
                    "X-Quota-Limit-Day": str(quota_info["requests_limit_day"]),
                    "X-Quota-Remaining-Day": str(quota_info["requests_limit_day"] - quota_info["requests_today"]),
                    "X-Quota-Limit-Minute": str(quota_info["requests_limit_minute"]),
                    "Retry-After": "60",
                },
            )

        # Record request
        quota_manager.record_request(user_id)

        # Process request
        response = await call_next(request)

        # Add quota headers
        response.headers["X-Quota-Plan"] = quota_info["plan"]
        response.headers["X-Quota-Remaining-Day"] = str(
            quota_info["requests_limit_day"] - quota_info["requests_today"] - 1
        )

        return response


# Service-specific rate limits
SERVICE_RATE_LIMITS: Dict[str, Dict[str, int]] = {
    "llm": {"requests_per_minute": 30, "tokens_per_request": 8000},
    "agents": {"requests_per_minute": 20, "sessions_per_hour": 10},
    "crypto": {"requests_per_minute": 60, "transactions_per_hour": 100},
    "blockchain": {"requests_per_minute": 100, "writes_per_minute": 20},
    "memory": {"requests_per_minute": 100, "ingests_per_minute": 50},
    "storage": {"requests_per_minute": 60, "uploads_per_hour": 100},
}


class ServiceRateLimiter:
    """Per-service rate limiting."""

    def __init__(self):
        # {service: {user_id: [timestamps]}}
        self.requests: Dict[str, Dict[str, List[float]]] = defaultdict(lambda: defaultdict(list))

    def _get_service_from_path(self, path: str) -> Optional[str]:
        """Extract service name from path."""
        parts = path.split("/")
        if len(parts) >= 3 and parts[1] == "api":
            return parts[2]
        return None

    def _clean_old_requests(self, service: str, user_id: str, window_seconds: int = 60):
        """Remove requests outside window."""
        now = time.time()
        cutoff = now - window_seconds
        self.requests[service][user_id] = [
            ts for ts in self.requests[service][user_id] if ts > cutoff
        ]

    def check_service_limit(
        self, service: str, user_id: str
    ) -> Tuple[bool, int, int]:
        """Check service-specific rate limit.
        
        Returns (allowed, remaining, reset_seconds).
        """
        limits = SERVICE_RATE_LIMITS.get(service, {"requests_per_minute": 100})
        max_requests = limits.get("requests_per_minute", 100)

        self._clean_old_requests(service, user_id)
        current = len(self.requests[service][user_id])

        if current >= max_requests:
            return False, 0, 60

        return True, max_requests - current - 1, 60

    def record_request(self, service: str, user_id: str):
        """Record a request for service."""
        self.requests[service][user_id].append(time.time())


service_rate_limiter = ServiceRateLimiter()


class ServiceRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-service rate limiting middleware."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Extract service
        service = service_rate_limiter._get_service_from_path(path)
        if not service or service not in SERVICE_RATE_LIMITS:
            return await call_next(request)

        # Get user ID
        user_id = request.headers.get("x-user-id", "anonymous")

        # Check service limit
        allowed, remaining, reset = service_rate_limiter.check_service_limit(service, user_id)

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "service_rate_limit_exceeded",
                    "service": service,
                    "message": f"Rate limit exceeded for {service} service",
                },
                headers={
                    "X-Service-RateLimit-Limit": str(SERVICE_RATE_LIMITS[service]["requests_per_minute"]),
                    "X-Service-RateLimit-Remaining": "0",
                    "X-Service-RateLimit-Reset": str(reset),
                    "Retry-After": str(reset),
                },
            )

        # Record request
        service_rate_limiter.record_request(service, user_id)

        # Process request
        response = await call_next(request)

        # Add headers
        response.headers["X-Service-RateLimit-Service"] = service
        response.headers["X-Service-RateLimit-Remaining"] = str(remaining)

        return response
