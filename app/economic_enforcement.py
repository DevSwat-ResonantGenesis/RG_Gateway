"""
Gateway Economic Enforcement Middleware.

This middleware enforces the UserEconomicState contract:
1. Reads economic state from billing_service on every authenticated request
2. Injects economic headers into downstream requests
3. Rejects requests if user has no economic state or is suspended
4. Checks credits/limits before execution (optional pre-check)
5. Deducts credits after execution (optional post-deduct)

This replaces the old QuotaMiddleware for economic enforcement.
QuotaMiddleware can still be used for rate limiting (requests/minute).
"""

import httpx
from typing import Callable, Optional, Dict
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response, JSONResponse

from .config import settings


# Billing service URL
BILLING_SERVICE_URL = getattr(settings, 'BILLING_URL', 'http://billing_service:8000')


# ============================================
# CREDIT COSTS (imported from billing_service)
# ============================================

# Path-based cost lookup (must match billing_service/app/credit_costs.py)
PATH_COSTS: Dict[str, int] = {
    # Chat
    "/chat/send": 500,
    "/chat/": 5,
    "/resonant-chat/message": 500,
    "/resonant-chat/": 5,
    
    # Agents
    "/agents/run": 2600,
    "/agents/": 10,
    
    # Workflows
    "/workflow/execute": 4000,
    "/workflow/": 10,
    
    # Memory
    "/memory/ingest": 120,
    "/memory/retrieve": 60,
    "/memory/": 20,
    "/hash-sphere/": 20,
    
    # IDE
    "/code/execute": 200,
    "/terminal/execute": 100,
    "/preview/": 200,
    "/ide/": 15,
    
    # ML
    "/ml/": 10,
    
    # Blockchain
    "/blockchain/": 50,
}


def get_path_cost(path: str, method: str = "GET") -> int:
    """Get the estimated cost for a request path."""
    # Check exact matches first
    if path in PATH_COSTS:
        return PATH_COSTS[path]
    
    # Check prefix matches
    for pattern, cost in PATH_COSTS.items():
        if pattern.endswith("/") and path.startswith(pattern):
            return cost
    
    # Default costs by method
    method_costs = {"GET": 1, "POST": 5, "PUT": 5, "PATCH": 5, "DELETE": 3}
    return method_costs.get(method.upper(), 1)


class EconomicEnforcementMiddleware(BaseHTTPMiddleware):
    """
    Middleware to enforce economic state on all authenticated requests.
    
    Flow:
    1. Skip for public/exempt paths
    2. Get user_id from auth headers (set by AuthMiddleware)
    3. Call billing_service to get economic state
    4. If not allowed, reject request
    5. Inject economic headers into downstream request
    6. Process request
    7. (Optional) Deduct credits after successful execution
    """

    # Paths exempt from economic enforcement
    EXEMPT_PATHS = {"/", "/health", "/metrics", "/docs", "/openapi.json", "/redoc"}
    EXEMPT_PREFIXES = (
        "/api/auth",
        "/auth",
        "/docs",
        "/openapi",
        "/redoc",
        # Public endpoints that don't require economic state
        "/public/",
        "/ws/",  # WebSocket auth handled separately
    )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        method = request.method.upper()

        # Skip for exempt paths
        if path in self.EXEMPT_PATHS or any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        # Skip for health endpoints
        if path.endswith("/health"):
            return await call_next(request)

        # Skip OPTIONS (CORS preflight)
        if method == "OPTIONS":
            return await call_next(request)

        # Get user_id from headers (set by AuthMiddleware)
        user_id = None
        for key, value in request.scope.get("headers", []):
            if key == b"x-user-id":
                user_id = value.decode("utf-8")
                break

        # If no user_id, let AuthMiddleware handle it
        if not user_id:
            return await call_next(request)

        # Get economic state from billing service
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{BILLING_SERVICE_URL}/economic-state/{user_id}/headers"
                )
        except httpx.RequestError as e:
            # Billing service unavailable - fail open or closed based on config
            if getattr(settings, 'ECONOMIC_FAIL_OPEN', False):
                return await call_next(request)
            return JSONResponse(
                status_code=503,
                content={
                    "error": "economic_service_unavailable",
                    "message": "Billing service unavailable",
                }
            )

        if resp.status_code != 200:
            return JSONResponse(
                status_code=502,
                content={
                    "error": "economic_state_error",
                    "message": "Failed to retrieve economic state",
                }
            )

        data = resp.json()

        # Check if request is allowed
        if not data.get("allowed", False):
            reason = data.get("reason", "Access denied")
            return JSONResponse(
                status_code=403,
                content={
                    "error": "economic_access_denied",
                    "message": reason,
                }
            )

        # Inject economic headers into downstream request
        economic_headers = data.get("headers", {})
        headers = list(request.scope.get("headers", []))
        
        for key, value in economic_headers.items():
            # Convert header name to lowercase bytes
            header_key = key.lower().encode("utf-8")
            header_value = str(value).encode("utf-8")
            
            # Remove existing header if present
            headers = [(k, v) for k, v in headers if k != header_key]
            
            # Add new header
            headers.append((header_key, header_value))

        request.scope["headers"] = headers

        # Process request
        response = await call_next(request)

        # Add economic info to response headers
        response.headers["X-Economic-Tier"] = economic_headers.get("X-Subscription-Tier", "unknown")
        response.headers["X-Economic-Credits"] = economic_headers.get("X-Credit-Balance", "0")

        return response


class CreditDeductionMiddleware(BaseHTTPMiddleware):
    """
    Middleware to check credits BEFORE and deduct AFTER execution.
    
    This should be added AFTER EconomicEnforcementMiddleware.
    
    Flow:
    1. Check if user has sufficient credits BEFORE execution
    2. If insufficient, return 402 Payment Required
    3. Process request
    4. Deduct credits AFTER successful execution (2xx status codes)
    """

    # Paths exempt from credit deduction
    EXEMPT_PATHS = {"/", "/health", "/metrics", "/docs"}
    EXEMPT_PREFIXES = (
        "/api/auth",
        "/auth",
        "/billing",
        "/public/",
    )

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        method = request.method.upper()

        # Skip for exempt paths
        if path in self.EXEMPT_PATHS or any(path.startswith(p) for p in self.EXEMPT_PREFIXES):
            return await call_next(request)

        # Skip for GET requests (read-only, no credit cost)
        if method == "GET":
            return await call_next(request)

        # Get user_id from headers
        user_id = None
        subscription_tier = None
        for key, value in request.scope.get("headers", []):
            if key == b"x-user-id":
                user_id = value.decode("utf-8")
            elif key == b"x-subscription-tier":
                subscription_tier = value.decode("utf-8")
            if user_id and subscription_tier:
                break

        if not user_id:
            return await call_next(request)

        # Calculate estimated cost
        credit_cost = get_path_cost(path, method)

        # ============================================
        # PRE-CHECK: Verify sufficient credits BEFORE execution
        # ============================================
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                check_resp = await client.post(
                    f"{BILLING_SERVICE_URL}/economic-state/{user_id}/check-credits",
                    params={"amount": credit_cost}
                )
                
                if check_resp.status_code == 200:
                    check_data = check_resp.json()
                    if not check_data.get("success", False):
                        tier_normalized = (subscription_tier or "").strip().lower()
                        if tier_normalized in {"developer", "free"}:
                            action_url = "/pricing"
                            detail_msg = "Credits exhausted. Upgrade to Plus to get more credits."
                        else:
                            action_url = "/billing"
                            detail_msg = "Credits exhausted. Buy more credits to continue."

                        # Insufficient credits - reject BEFORE execution
                        return JSONResponse(
                            status_code=402,
                            content={
                                "error": "insufficient_credits",
                                "detail": detail_msg,
                                "message": detail_msg,
                                "action_url": action_url,
                                "required": credit_cost,
                                "available": check_data.get("new_balance", 0),
                            }
                        )
        except httpx.RequestError:
            # If billing service unavailable, fail open or closed based on config
            if not getattr(settings, 'CREDIT_CHECK_FAIL_OPEN', False):
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "credit_check_unavailable",
                        "message": "Could not verify credit balance",
                    }
                )

        # ============================================
        # EXECUTE: Process the request
        # ============================================
        response = await call_next(request)

        # ============================================
        # POST-DEDUCT: Deduct credits AFTER successful execution
        # ============================================
        if 200 <= response.status_code < 300:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{BILLING_SERVICE_URL}/economic-state/{user_id}/deduct",
                        json={
                            "amount": credit_cost,
                            "reference_type": "api_call",
                            "description": f"{method} {path}",
                        }
                    )
            except httpx.RequestError:
                # Log but don't fail the request (credits already checked)
                pass

        return response


class FeatureGateMiddleware(BaseHTTPMiddleware):
    """
    Middleware to gate features based on subscription tier.
    
    Checks if user has access to specific features before allowing request.
    """

    # Feature requirements by path pattern
    FEATURE_REQUIREMENTS = {
        "/ide/": "ide_access",
        "/code/execute": "code_execution",
        "/terminal/execute": "code_execution",
        "/blockchain/": "blockchain_access",
        "/hash-sphere/": "hash_sphere_access",
    }

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path

        # Check if path requires a feature
        required_feature = None
        for pattern, feature in self.FEATURE_REQUIREMENTS.items():
            if pattern in path:
                required_feature = feature
                break

        if not required_feature:
            return await call_next(request)

        # Get user_id from headers
        user_id = None
        for key, value in request.scope.get("headers", []):
            if key == b"x-user-id":
                user_id = value.decode("utf-8")
                break

        if not user_id:
            return await call_next(request)

        # Check feature access
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{BILLING_SERVICE_URL}/economic-state/{user_id}/check-feature/{required_feature}"
                )
        except httpx.RequestError:
            # Fail open or closed based on config
            if getattr(settings, 'FEATURE_GATE_FAIL_OPEN', False):
                return await call_next(request)
            return JSONResponse(
                status_code=503,
                content={
                    "error": "feature_check_unavailable",
                    "message": "Could not verify feature access",
                }
            )

        if resp.status_code != 200:
            return JSONResponse(
                status_code=502,
                content={
                    "error": "feature_check_error",
                    "message": "Failed to check feature access",
                }
            )

        data = resp.json()

        if not data.get("allowed", False):
            return JSONResponse(
                status_code=403,
                content={
                    "error": "feature_not_available",
                    "feature": required_feature,
                    "message": data.get("reason", f"Feature '{required_feature}' not available"),
                }
            )

        return await call_next(request)
