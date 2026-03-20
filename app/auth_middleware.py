from typing import Callable
import logging

import httpx
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from .config import settings

logger = logging.getLogger(__name__)


def add_cors_headers(response: Response, request: Request) -> Response:
    """Add CORS headers to response with strict domain validation."""
    origin = request.headers.get("origin", "")
    
    # Define allowed origins from environment variable
    import os
    frontend_url = os.getenv("AUTH_FRONTEND_URL", "https://dev-swat.com")
    allowed_origins = [
        frontend_url,
        f"https://www.{frontend_url.replace('https://', '')}",
        f"https://api.{frontend_url.replace('https://', '')}",
    ]
    
    # Only set CORS header if origin is allowed
    if origin in allowed_origins:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS, PATCH"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Requested-With, Accept, Origin, X-API-Key"
    
    return response


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        for _ in range(3):
            if not path.startswith("/api/v1/api/v1/"):
                break
            path = path.replace("/api/v1/api/v1/", "/api/v1/", 1)
        request.scope["path"] = path
        method = request.method.upper()

        # Allow loading the Code Visualizer UI shell without auth so iframe reloads
        # (e.g. Safari memory pressure reload) don't hard-fail with a 401 body.
        # IMPORTANT: keep all Code Visualizer API/scan routes protected.
        if method == "GET" and path in {"/api/v1/code-visualizer-ui", "/api/v1/code-visualizer-ui/"}:
            return await call_next(request)

        # Allow unauthenticated access to auth and essential public endpoints ONLY.
        # SECURITY: Minimal set — every path here is reachable without any token.
        public_paths = {
            "/",
            "/health",
            "/api/v1/login",
            "/api/v1/register",
            "/api/v1/api/auth/login",
            "/api/v1/api/auth/signup",
            "/api/v1/api/auth/providers",
        }
        # SECURITY: Minimal public prefixes — only what MUST be unauthenticated.
        # REMOVED: /docs, /openapi, /redoc (exposed full 509KB API schema)
        # REMOVED: /owner/dashboard/* (leaked CPU, RAM, disk, daemon info)
        # REMOVED: /admin/revoke/*, /debug/middleware (admin operations)
        # REMOVED: /api/v1/memory/stats (leaked memory counts)
        public_prefixes = (
            # Auth endpoints (must be public for login/signup/OAuth)
            "/api/auth", "/api/v1/auth", "/auth", "/oauth",
            "/api/v1/github/oauth/",
            "/api/v1/api/auth",
            "/owner/auth/",
            # WebSocket endpoints - auth handled post-connect at application level
            "/ws/",
            "/api/v1/ws/",
            # Local LLM tunnel - auth handled in endpoint
            "/local-llm/",
            "/api/v1/local-llm/",
            # V8 API routes - auth handled in route handlers with owner token verification
            "/api/v1/v8/api/",
            # Skills routes - chat_service handles its own auth
            "/skills/",
            "/api/skills/",
            "/api/v1/skills/",
            # Public static content endpoints
            "/public/",
            "/v1/public/",
            "/api/v1/public/",
            # Public agentic chat (guest mode — no auth, rate limited)
            "/public/agentic-chat/",
            "/api/public/agentic-chat/",
            # Pricing endpoint (public - needed for pricing page)
            "/billing/pricing",
            "/billing/packs",
            "/api/v1/api/billing/pricing",
            "/api/v1/api/billing/packs",
            "/api/v1/api/billing/pricing/",
            "/api/v1/api/billing/packs/",
            "/api/v1/api/v1/api/billing/pricing",
            "/api/v1/api/v1/api/billing/packs",
            "/api/v1/api/v1/api/billing/pricing/",
            "/api/v1/api/v1/api/billing/packs/",
            # Stripe webhook (Stripe sends without auth)
            "/billing/webhook/stripe",
            "/api/billing/stripe/webhook",
            "/webhook/stripe",
            # External webhook triggers
            "/webhooks/agent/",
            "/webhooks/github/",
            "/api/webhooks/agent/",
            "/api/webhooks/github/",
            "/api/v1/webhooks/agent/",
            "/api/v1/webhooks/github/",
            # Health-only endpoints (no data exposure)
            "/api/v1/memory/health",
            "/hash-sphere/health",
            # Memory visualizer HTML shell (data APIs still require auth)
            "/api/v1/memory/visualizer/",
            # API key validation (needed during signup before auth)
            "/user/api-keys/validate",
            # Marketplace service (public read-only catalogs)
            "/api/marketplace/marketplace/categories",
            "/api/marketplace/marketplace/stats",
            "/api/marketplace/marketplace/featured",
            # Node/decentralized network
            "/api/v1/node/",
            # Resonant Chat public endpoints (agent/team lists only)
            "/resonant-chat/agents/list",
            "/resonant-chat/teams",
            "/api/resonant-chat/agents/list",
            "/api/resonant-chat/teams",
            # Rabbit: moved to optional_auth (GET public, writes require login)
            # Storage downloads (public for post images)
            "/api/storage/download",
            "/api/v1/storage/download",
            "/storage/download",
            # Rabbit post OG pages (social media sharing)
            "/api/v1/rabbit/posts/",
        )

        import os
        if os.getenv("STATE_PHYSICS_PUBLIC", "false").strip().lower() in {"1", "true", "yes"}:
            public_prefixes = public_prefixes + ("/state-physics", "/api/v1/state-physics")
        
        # Allow GET on marketplace listings without auth
        if path.startswith("/api/marketplace/marketplace/listings") and method == "GET":
            return await call_next(request)

        if (
            path in public_paths
            or any(path.startswith(p) for p in public_prefixes)
            or path.endswith("/health")  # Allow health checks for all services
            or method == "OPTIONS"
        ):
            return await call_next(request)

        # Optional auth endpoints: authenticate if cookie present, pass through if not.
        # These endpoints work for both anonymous and authenticated users but return
        # richer data (e.g. BYOK key status) when the user is identified.
        optional_auth_paths = (
            "/resonant-chat/providers",
            "/api/resonant-chat/providers",
            "/api/v1/rabbit",
            "/rabbit",
        )
        is_optional_auth = any(path == p or path.startswith(p + "/") for p in optional_auth_paths)
        if is_optional_auth:
            opt_token = None
            opt_auth_header = request.headers.get("authorization")
            if opt_auth_header and opt_auth_header.lower().startswith("bearer "):
                candidate = opt_auth_header.split(" ", 1)[1].strip()
                if not candidate.startswith("RG-"):
                    opt_token = candidate
            else:
                opt_token = request.cookies.get("rg_access_token")

            if opt_token:
                try:
                    from .auth_cache import auth_cache
                    cached = auth_cache.get(opt_token)
                    if cached:
                        data = cached
                    else:
                        async with httpx.AsyncClient() as client:
                            verify_resp = await client.post(
                                f"{settings.AUTH_URL}/auth/verify",
                                json={"token": opt_token},
                                timeout=5.0,
                            )
                        if verify_resp.status_code == 200:
                            data = verify_resp.json()
                            auth_cache.set(opt_token, data)
                        else:
                            data = {}

                    user_id = data.get("user_id")
                    if data.get("valid") and user_id:
                        headers = list(request.scope.get("headers", []))
                        headers.append((b"x-user-id", str(user_id).encode("utf-8")))
                        headers.append((b"x-user-role", str(data.get("role", "user")).encode("utf-8")))
                        headers.append((b"x-user-plan", str(data.get("plan", "free")).encode("utf-8")))
                        headers.append((b"x-org-id", str(data.get("org_id") or user_id).encode("utf-8")))
                        request.scope["headers"] = headers
                        request.state.user_id = user_id
                except Exception as e:
                    logger.debug(f"[AUTH] Optional auth failed for {path}: {e}")

            return await call_next(request)

        # Check for token in Authorization header OR HttpOnly cookie FIRST
        # This ensures real users get their own user_id even in DEV_MODE
        auth_header = request.headers.get("authorization")
        token = None
        api_key = (request.headers.get("x-api-key") or "").strip() or None

        if auth_header and auth_header.lower().startswith("bearer "):
            candidate = auth_header.split(" ", 1)[1].strip()
            # If this is an RG- API key, treat it as API key auth, not JWT.
            if candidate.startswith("RG-"):
                api_key = candidate
            else:
                token = candidate
        else:
            # Check for HttpOnly cookie (rg_access_token)
            token = request.cookies.get("rg_access_token")

        # DEV_MODE bypass for local development ONLY
        # In DEV_MODE, we still require user identification but skip token validation
        # This allows testing without valid JWT tokens while maintaining user isolation
        if settings.DEV_MODE and settings.ENVIRONMENT == "development" and not token:
            client_ip = request.client.host if request.client else ""
            is_localhost = client_ip in {"127.0.0.1", "::1", "localhost"}

            allow_bypass = bool(getattr(settings, "ALLOW_DEV_MODE_BYPASS", False))
            localhost_only = bool(getattr(settings, "DEV_MODE_LOCALHOST_ONLY", True))

            # Extra hardening:
            # - If ALLOW_DEV_MODE_BYPASS is false, only allow when the request originates from localhost
            #   (useful to avoid accidental exposure when ENVIRONMENT is mis-set).
            if not allow_bypass and localhost_only and not is_localhost:
                logger.warning(
                    f"[AUTH] DEV_MODE bypass denied for non-localhost client_ip={client_ip} on {request.method} {path}"
                )
                return add_cors_headers(Response(
                    status_code=401,
                    content=b"Missing or invalid Authorization header",
                ), request)

            # In dev mode, use X-User-ID header if provided, otherwise generate one
            dev_user_id = request.headers.get("x-user-id")
            if not dev_user_id:
                # Generate a consistent dev user ID as a valid UUID based on client IP
                client_ip = request.client.host if request.client else "localhost"
                import hashlib
                import uuid
                # Create a deterministic UUID from the client IP
                hash_bytes = hashlib.md5(client_ip.encode()).digest()
                dev_user_id = str(uuid.UUID(bytes=hash_bytes))
            
            request.state.user_id = dev_user_id
            request.state.role = request.headers.get("x-role", "user")
            request.state.plan = request.headers.get("x-plan", "developer")
            request.state.org_id = request.headers.get("x-org-id", "dev-org")
            
            response = await call_next(request)
            response.headers["x-user-id"] = dev_user_id
            response.headers["x-role"] = request.state.role
            response.headers["x-plan"] = request.state.plan
            response.headers["x-org-id"] = request.state.org_id
            return response
        
        if not token and api_key:
            # API key auth path
            try:
                async with httpx.AsyncClient() as client:
                    verify_resp = await client.post(
                        f"{settings.AUTH_URL}/auth/api-keys/verify",
                        json={"api_key": api_key},
                        timeout=5.0,
                    )
            except httpx.RequestError:
                return add_cors_headers(Response(
                    status_code=502,
                    content=b"Auth service unavailable",
                ), request)

            if verify_resp.status_code != 200:
                return add_cors_headers(Response(
                    status_code=verify_resp.status_code,
                    content=verify_resp.content,
                ), request)

            data = verify_resp.json() or {}
            user_id = data.get("user_id")
            org_id = data.get("org_id") or user_id
            user_role = data.get("role", "user")
            user_plan = data.get("plan", "developer")

            if not data.get("valid") or not user_id:
                return add_cors_headers(Response(status_code=401, content=b"Invalid API key"), request)

            headers = list(request.scope.get("headers", []))
            headers.append((b"x-user-id", str(user_id).encode("utf-8")))
            headers.append((b"x-user-role", str(user_role).encode("utf-8")))
            headers.append((b"x-user-plan", str(user_plan).encode("utf-8")))
            headers.append((b"x-org-id", str(org_id).encode("utf-8")))
            # Propagate unlimited_credits for billing bypass without role elevation
            unlimited_credits = data.get("unlimited_credits", False)
            headers.append((b"x-unlimited-credits", str(unlimited_credits).lower().encode("utf-8")))
            request.scope["headers"] = headers

            request.state.user_id = user_id
            request.state.role = user_role
            request.state.org_id = org_id

            response = await call_next(request)
            return response

        if not token:
            logger.warning(f"[AUTH] 401 - No token found for {request.method} {path}")
            return add_cors_headers(Response(
                status_code=401,
                content=b"Missing or invalid Authorization header",
            ), request)

        # Check cache first
        from .auth_cache import auth_cache
        from .revocation_manager_redis import revocation_manager
        cached_result = auth_cache.get(token)
        
        if cached_result:
            data = cached_result
        else:
            # Delegate token validation to auth_service
            try:
                async with httpx.AsyncClient() as client:
                    verify_resp = await client.post(
                        f"{settings.AUTH_URL}/auth/verify",
                        json={"token": token},
                        timeout=5.0,
                    )
            except httpx.RequestError:
                return add_cors_headers(Response(
                    status_code=502,
                    content=b"Auth service unavailable",
                ), request)

            if verify_resp.status_code != 200:
                logger.warning(f"[AUTH] {verify_resp.status_code} - Auth service rejected token for {request.method} {path}: {verify_resp.content[:200]}")
                return add_cors_headers(Response(
                    status_code=verify_resp.status_code,
                    content=verify_resp.content,
                ), request)

            data = verify_resp.json()
            
            # Cache the result
            auth_cache.set(token, data)
        
        user_id = data.get("user_id")
        if not data.get("valid") or not user_id:
            logger.warning(f"[AUTH] 401 - Invalid token data for {request.method} {path}: valid={data.get('valid')}, user_id={user_id}")
            return add_cors_headers(Response(status_code=401, content=b"Invalid token"), request)

        # Check for token revocation (decode token to get claims)
        try:
            from jose import jwt
            token_claims = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=["HS256"])
            
            # Check if token is revoked
            is_revoked, revocation_reason = await revocation_manager.is_token_revoked(token_claims)
            if is_revoked:
                # Clear from cache
                auth_cache.invalidate(token=token)
                return add_cors_headers(Response(
                    status_code=401,
                    content=f"Token revoked: {revocation_reason}".encode(),
                ), request)
        except Exception:
            # If we can't decode token, continue (auth service already validated)
            pass

        # Get user role, plan, and org from auth response
        user_role = data.get("role", "user")
        user_plan = data.get("plan", "free")
        org_id = data.get("org_id") or data.get("organization_id") or user_id  # Fallback to user_id

        # Inject user headers into downstream request
        headers = list(request.scope.get("headers", []))
        headers.append((b"x-user-id", str(user_id).encode("utf-8")))
        headers.append((b"x-user-role", str(user_role).encode("utf-8")))
        headers.append((b"x-user-plan", str(user_plan).encode("utf-8")))
        headers.append((b"x-org-id", str(org_id).encode("utf-8")))
        
        # Pass superuser status (for owner dashboard access validation)
        is_superuser = data.get("is_superuser", False)
        headers.append((b"x-is-superuser", str(is_superuser).lower().encode("utf-8")))
        
        # Pass unlimited_credits flag for billing bypass without role elevation
        unlimited_credits = data.get("unlimited_credits", False)
        headers.append((b"x-unlimited-credits", str(unlimited_credits).lower().encode("utf-8")))
        
        # Add crypto identity headers for enhanced security and Hash Sphere integration
        if "crypto_hash" in data and data["crypto_hash"]:
            headers.append((b"x-crypto-hash", str(data["crypto_hash"]).encode("utf-8")))
        if "user_hash" in data and data["user_hash"]:
            headers.append((b"x-user-hash", str(data["user_hash"]).encode("utf-8")))
        if "universe_id" in data and data["universe_id"]:
            headers.append((b"x-universe-id", str(data["universe_id"]).encode("utf-8")))
        
        request.scope["headers"] = headers
        
        # Also set on request.state for proxy function
        request.state.user_id = user_id
        request.state.role = user_role
        request.state.org_id = org_id
        request.state.unlimited_credits = unlimited_credits
        request.state.crypto_hash = data.get("crypto_hash")
        request.state.user_hash = data.get("user_hash")
        request.state.universe_id = data.get("universe_id")

        response = await call_next(request)
        return response


async def verify_token_for_ws(token: str) -> str | None:
    """Verify a JWT token for WebSocket connections. Returns user_id or None."""
    if not token:
        return None
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://auth_service:8000/auth/verify",
                json={"token": token},
                timeout=5.0,
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("valid"):
                    return data.get("user_id")
    except Exception as e:
        logger.warning(f"WS token verify failed: {e}")
    return None
