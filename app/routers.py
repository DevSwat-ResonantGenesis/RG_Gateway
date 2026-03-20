from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from starlette.responses import JSONResponse
import httpx
import websockets
import asyncio
import json

from .reverse_proxy import proxy, proxy_public
from .voice_ws import voice_session_skeleton

router = APIRouter()


# ============================================
# WEBSOCKET ROUTES
# ============================================
# NOTE: Specific routes MUST come before catch-all routes!

# Provider status - public, no auth required
@router.websocket("/ws/provider-status")
async def websocket_provider_status_proxy(websocket: WebSocket):
    """Proxy WebSocket connections for live provider status updates."""
    import logging
    logger = logging.getLogger(__name__)
    
    from .config import settings
    chat_url = settings.CHAT_URL.replace("http://", "ws://")
    provider_ws_url = f"{chat_url}/ws/provider-status"
    
    logger.info(f"Provider status WebSocket proxy: connecting to {provider_ws_url}")
    
    try:
        await websocket.accept()
        logger.info("Client WebSocket accepted")
        
        async with websockets.connect(provider_ws_url) as backend_ws:
            logger.info("Backend WebSocket connected")
            
            async def client_to_backend():
                try:
                    while True:
                        message = await websocket.receive_text()
                        await backend_ws.send(message)
                except WebSocketDisconnect:
                    logger.info("Client disconnected")
            
            async def backend_to_client():
                try:
                    async for message in backend_ws:
                        await websocket.send_text(message)
                except Exception as e:
                    logger.warning(f"Backend to client error: {e}")
            
            done, pending = await asyncio.wait(
                [asyncio.create_task(client_to_backend()), asyncio.create_task(backend_to_client())],
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
    except websockets.exceptions.InvalidStatusCode as e:
        logger.error(f"Backend rejected WebSocket: {e.status_code}")
        try:
            await websocket.close(code=1011)
        except:
            pass
    except Exception as e:
        logger.error(f"Provider status WebSocket error: {type(e).__name__}: {e}")
        try:
            await websocket.close(code=1011)
        except:
            pass


# Chat WebSocket - specific route before catch-all
@router.websocket("/ws/chat/{chat_id}")
async def websocket_chat_proxy(websocket: WebSocket, chat_id: str):
    """Proxy WebSocket connections to chat service."""
    chat_ws_url = f"ws://chat_service:8000/ws/chat/{chat_id}"
    
    try:
        await websocket.accept()
        
        async with websockets.connect(chat_ws_url) as backend_ws:
            async def client_to_backend():
                try:
                    while True:
                        message = await websocket.receive_text()
                        await backend_ws.send(message)
                except WebSocketDisconnect:
                    pass
            
            async def backend_to_client():
                try:
                    async for message in backend_ws:
                        await websocket.send_text(message)
                except Exception:
                    pass
            
            done, pending = await asyncio.wait(
                [asyncio.create_task(client_to_backend()), asyncio.create_task(backend_to_client())],
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
    except websockets.exceptions.InvalidStatusCode as e:
        try:
            await websocket.send_json({"type": "error", "error": f"Backend rejected: {e.status_code}"})
            await websocket.close()
        except:
            pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
            await websocket.close()
        except:
            pass


# Voice duplex WebSocket - must be before /ws/{client_id} catch-all
@router.websocket("/voice/session")
async def websocket_voice_session(websocket: WebSocket):
    """Gateway skeleton endpoint for real-time duplex voice sessions.

    Full route exposed to clients:
    - /api/v1/voice/session
    """
    await voice_session_skeleton.handle(websocket)


# Local LLM tunnel - per-user WebSocket relay to their local Ollama/LM Studio
@router.websocket("/ws/local-llm/tunnel")
async def websocket_local_llm_tunnel(websocket: WebSocket):
    """WebSocket tunnel for per-user local LLM connections.
    
    Flow:
    1. Browser connects with auth token
    2. Browser sends: {"type":"auth","token":"jwt","endpoint_url":"http://localhost:11434"}
    3. Gateway validates, registers tunnel
    4. Gateway sends LLM requests → browser → local Ollama → browser → gateway
    5. Browser sends heartbeats to keep alive
    """
    import logging
    _logger = logging.getLogger("local_llm_tunnel")
    from .services.local_llm_tunnel import tunnel_manager
    
    user_id = None
    try:
        await websocket.accept()
        
        # Phase 1: Auth
        try:
            auth_msg = await asyncio.wait_for(websocket.receive_json(), timeout=15.0)
        except asyncio.TimeoutError:
            await websocket.send_json({"type": "error", "error": "Auth timeout"})
            await websocket.close(code=4001)
            return
        
        if auth_msg.get("type") != "auth":
            await websocket.send_json({"type": "error", "error": "First message must be auth"})
            await websocket.close(code=4001)
            return
        
        token = auth_msg.get("token", "")
        endpoint_url = auth_msg.get("endpoint_url", "http://localhost:11434")
        
        # Validate JWT
        from .auth_middleware import verify_token_for_ws
        uid = await verify_token_for_ws(token)
        if not uid:
            await websocket.send_json({"type": "error", "error": "Invalid token"})
            await websocket.close(code=4003)
            return
        
        user_id = uid
        tunnel = await tunnel_manager.register(user_id, websocket, endpoint_url)
        
        await websocket.send_json({
            "type": "auth_success",
            "user_id": user_id,
            "endpoint_url": endpoint_url,
            "message": "Local LLM tunnel active",
        })
        _logger.info(f"Local LLM tunnel opened: user={user_id} endpoint={endpoint_url}")
        
        # Phase 2: Message loop
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type", "")
            
            if msg_type == "heartbeat":
                tunnel.last_heartbeat = __import__("time").time()
                await websocket.send_json({"type": "heartbeat_ack"})
            
            elif msg_type == "llm_response":
                # Browser returning a completion result
                request_id = msg.get("request_id", "")
                response = msg.get("response", {})
                error = msg.get("error")
                if error:
                    response = {"error": error}
                tunnel.resolve_request(request_id, response)
            
            elif msg_type == "models_update":
                tunnel.models = msg.get("models", [])
            
            elif msg_type == "ping":
                await websocket.send_json({"type": "pong"})
            
    except WebSocketDisconnect:
        _logger.info(f"Local LLM tunnel disconnected: user={user_id}")
    except Exception as e:
        _logger.error(f"Local LLM tunnel error: {type(e).__name__}: {e}")
    finally:
        if user_id:
            tunnel_manager.unregister(user_id)


# IDE WebSocket - catch-all route MUST be last
@router.websocket("/ws/{client_id}")
async def websocket_ide_proxy(websocket: WebSocket, client_id: str):
    """Proxy WebSocket connections to IDE service for DSID-P Accelerator.
    
    Authentication & Execution Binding Protocol:
    1. Client connects with client_id in URL
    2. Client sends auth: {"type": "auth", "token": "jwt"}
    3. Gateway validates token, responds: {"type": "auth_success", "user_id": "...", "user_dsid": "dsid-u-..."}
    4. Client sends bind: {"type": "bind_execution", "execution_id": "...", "plane": "IDE", "agent_dsid": "dsid-a-..."}
    5. Gateway validates user owns execution, responds: {"type": "bind_success", "execution_id": "..."}
    6. Streams filtered by allowed_execution_ids
    """
    ide_ws_url = f"ws://ide_platform_service:8080/ws/{client_id}"
    
    # Execution stream authorization state
    allowed_execution_ids: set = set()
    user_id = None
    user_dsid = None
    
    try:
        await websocket.accept()
        
        # ============== PHASE 1: AUTHENTICATION ==============
        try:
            auth_data = await asyncio.wait_for(
                websocket.receive_json(),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            await websocket.send_json({"type": "error", "error": "Authentication timeout"})
            await websocket.close(code=4001)
            return
        
        if auth_data.get("type") != "auth":
            await websocket.send_json({"type": "error", "error": "First message must be authentication"})
            await websocket.close(code=4001)
            return
        
        # Get token from auth message or allow user_id for dev mode
        token = auth_data.get("token")
        user_id = auth_data.get("user_id")
        
        if token:
            # Validate token with auth service
            try:
                async with httpx.AsyncClient() as client:
                    verify_resp = await client.post(
                        "http://auth_service:8000/auth/verify",
                        json={"token": token},
                        timeout=5.0,
                    )
                    if verify_resp.status_code == 200:
                        data = verify_resp.json()
                        if data.get("valid"):
                            user_id = data.get("user_id")
                            user_dsid = data.get("dsid") or f"dsid-u-{user_id[:16]}" if user_id else None
                        else:
                            await websocket.send_json({"type": "error", "error": "Invalid token"})
                            await websocket.close(code=4002)
                            return
                    else:
                        await websocket.send_json({"type": "error", "error": "Token validation failed"})
                        await websocket.close(code=4002)
                        return
            except httpx.RequestError:
                await websocket.send_json({"type": "error", "error": "Auth service unavailable"})
                await websocket.close(code=4003)
                return
        
        if not user_id:
            await websocket.send_json({"type": "error", "error": "Authentication required"})
            await websocket.close(code=4002)
            return
        
        # Generate DSID if not provided
        if not user_dsid:
            import hashlib
            user_hash = hashlib.sha256(user_id.encode()).hexdigest()[:16]
            checksum = hashlib.sha256(f"dsid-u-{user_hash}".encode()).hexdigest()[:4]
            user_dsid = f"dsid-u-{user_hash}-{checksum}"
        
        # Send auth success with DSID
        await websocket.send_json({
            "type": "auth_success",
            "user_id": user_id,
            "user_dsid": user_dsid,
            "client_id": client_id,
        })
        
        # ============== PHASE 2: EXECUTION BINDING & PROXY ==============
        try:
            async with websockets.connect(ide_ws_url) as backend_ws:
                # Forward user context to backend
                await backend_ws.send(json.dumps({
                    "type": "user_context",
                    "user_id": user_id,
                    "user_dsid": user_dsid,
                    "client_id": client_id,
                }))
                
                async def client_to_backend():
                    nonlocal allowed_execution_ids
                    try:
                        while True:
                            message = await websocket.receive_text()
                            try:
                                msg_data = json.loads(message)
                                msg_type = msg_data.get("type")
                                
                                # Handle bind_execution requests
                                if msg_type == "bind_execution":
                                    execution_id = msg_data.get("execution_id")
                                    plane = msg_data.get("plane", "IDE")
                                    agent_dsid = msg_data.get("agent_dsid")
                                    initiator_dsid = msg_data.get("initiator_dsid") or user_dsid
                                    
                                    if not execution_id:
                                        await websocket.send_json({
                                            "type": "error",
                                            "error": "execution_id required for bind_execution"
                                        })
                                        continue
                                    
                                    # TODO: Validate user owns this execution via IDE service
                                    # For now, add to allowed set
                                    allowed_execution_ids.add(execution_id)
                                    
                                    # Forward to backend with full context
                                    await backend_ws.send(json.dumps({
                                        "type": "bind_execution",
                                        "execution_id": execution_id,
                                        "plane": plane,
                                        "agent_dsid": agent_dsid,
                                        "initiator_dsid": initiator_dsid,
                                        "user_id": user_id,
                                        "user_dsid": user_dsid,
                                    }))
                                    
                                    await websocket.send_json({
                                        "type": "bind_success",
                                        "execution_id": execution_id,
                                        "plane": plane,
                                    })
                                    continue
                                
                                # Handle unbind_execution
                                if msg_type == "unbind_execution":
                                    execution_id = msg_data.get("execution_id")
                                    if execution_id and execution_id in allowed_execution_ids:
                                        allowed_execution_ids.discard(execution_id)
                                        await backend_ws.send(message)
                                        await websocket.send_json({
                                            "type": "unbind_success",
                                            "execution_id": execution_id,
                                        })
                                    continue
                                
                            except json.JSONDecodeError:
                                pass
                            
                            # Forward all other messages
                            await backend_ws.send(message)
                    except WebSocketDisconnect:
                        pass
                    except Exception:
                        pass
                
                async def backend_to_client():
                    try:
                        async for message in backend_ws:
                            # Filter execution events by allowed_execution_ids
                            try:
                                msg_data = json.loads(message)
                                exec_id = msg_data.get("execution_id")
                                
                                # If message has execution_id, verify it's allowed
                                if exec_id and allowed_execution_ids and exec_id not in allowed_execution_ids:
                                    # Drop unauthorized execution stream
                                    continue
                                
                            except json.JSONDecodeError:
                                pass
                            
                            await websocket.send_text(message)
                    except Exception:
                        pass
                
                done, pending = await asyncio.wait(
                    [
                        asyncio.create_task(client_to_backend()),
                        asyncio.create_task(backend_to_client()),
                    ],
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                for task in pending:
                    task.cancel()
        except websockets.exceptions.InvalidStatusCode as e:
            await websocket.send_json({"type": "error", "error": f"IDE backend rejected: {e.status_code}"})
        except Exception as e:
            await websocket.send_json({"type": "error", "error": f"IDE connection failed: {str(e)}"})
                
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "error": str(e)})
            await websocket.close()
        except:
            pass


# ============================================
# BLOCKCHAIN SERVICE ROUTES
# ============================================

@router.api_route("/blockchain/status", methods=["GET", "OPTIONS"])
async def blockchain_status_route(request: Request):
    """Blockchain status route."""
    return await proxy("blockchain", "blockchain/status", request)


@router.api_route("/blockchain/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def blockchain_route(path: str, request: Request):
    """Blockchain Service API routes."""
    return await proxy("blockchain", f"blockchain/{path}", request)


# ============================================
# CRYPTO SERVICE ROUTES
# ============================================

@router.api_route("/crypto/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def crypto_route(path: str, request: Request):
    """Crypto Service API routes."""
    return await proxy("crypto", f"crypto/{path}", request)


# ============================================
# NOTIFICATION SERVICE ROUTES
# ============================================

@router.api_route("/notifications", methods=["GET", "POST", "OPTIONS"])
async def notifications_base_route(request: Request):
    """Notifications API base route."""
    return await proxy("notification", "notifications", request)


@router.api_route("/notifications/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def notifications_route(path: str, request: Request):
    """Notifications API routes."""
    return await proxy("notification", f"notifications/{path}", request)


# ============================================
# WORKFLOW SERVICE ROUTES
# ============================================

@router.api_route("/workflows", methods=["GET", "POST", "OPTIONS"])
async def workflows_base_route(request: Request):
    """Workflows API base route."""
    return await proxy("workflow", "workflow/workflows", request)


@router.api_route("/workflows/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def workflows_route(path: str, request: Request):
    """Workflows API routes."""
    return await proxy("workflow", f"workflow/workflows/{path}", request)


@router.api_route("/workflow/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def workflow_service_route(path: str, request: Request):
    """Workflow service routes (runs/events/etc)."""
    return await proxy("workflow", f"workflow/{path}", request)


@router.api_route("/workflow", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def workflow_service_base_route(request: Request):
    """Workflow service base route."""
    return await proxy("workflow", "workflow", request)


# ============================================
# STORAGE SERVICE ROUTES
# ============================================

@router.api_route("/storage/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def storage_route(path: str, request: Request):
    """Storage Service API routes."""
    return await proxy("storage", f"storage/{path}", request)


# ============================================
# CODE VISUALIZER ROUTES
# ============================================

@router.api_route("/scan/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def code_visualizer_scan_route(path: str, request: Request):
    """Code visualizer scan routes."""
    return await proxy("code-visualizer", f"api/v1/scan/{path}", request)


# ============================================
# RABBIT ROUTES
# ============================================

@router.api_route("/rabbit/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def rabbit_route(path: str, request: Request):
    """Rabbit API routes."""
    return await proxy("rabbit", f"rabbit/{path}", request)


@router.api_route("/api/v1/rabbit/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def api_v1_rabbit_route(path: str, request: Request):
    """Rabbit API routes (v1)."""
    return await proxy("rabbit", f"rabbit/{path}", request)


# ============================================
# COGNITIVE SERVICE ROUTES
# ============================================

@router.api_route("/cognitive/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def cognitive_route(path: str, request: Request):
    """Cognitive Service API routes."""
    return await proxy("cognitive", f"cognitive/{path}", request)


# ============================================
# LLM SERVICE ROUTES
# ============================================

@router.api_route("/llm/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def llm_route(path: str, request: Request):
    """LLM Service API routes."""
    return await proxy("llm", f"llm/{path}", request)


# ============================================
# AGENTS SERVICE ROUTES - moved to line 524 to avoid duplicates


# Specific /api/auth routes MUST come before the catch-all
@router.api_route("/api/auth/signup", methods=["POST", "OPTIONS"])
@router.api_route("/api/auth/signup/", methods=["POST", "OPTIONS"])
async def api_auth_signup_route(request: Request):
    """API auth signup route - proxies to auth service register endpoint."""
    return await proxy_public("auth", "auth/register", request)


# Frontend compatibility routes - /api/auth/* (no /v1)
@router.api_route("/api/auth/login", methods=["POST", "OPTIONS"])
@router.api_route("/api/auth/login/", methods=["POST", "OPTIONS"])
@router.api_route("/api/v1/auth/login", methods=["POST", "OPTIONS"])
@router.api_route("/api/v1/auth/login/", methods=["POST", "OPTIONS"])
async def api_auth_login_route(request: Request):
    """API auth login route - proxies to auth service."""
    return await proxy_public("auth", "auth/login", request)


@router.api_route("/api/auth/providers", methods=["GET", "OPTIONS"])
@router.api_route("/api/auth/providers/", methods=["GET", "OPTIONS"])
async def api_auth_providers_route(request: Request):
    """API auth providers route."""
    return await proxy("auth", "auth/sso/providers", request)


# Frontend compatibility routes - /api/billing/* (no /v1)
@router.api_route("/api/billing/pricing", methods=["GET", "OPTIONS"])
@router.api_route("/api/billing/pricing/", methods=["GET", "OPTIONS"])
async def api_billing_pricing_route(request: Request):
    """API billing pricing route."""
    return await proxy_public("billing-user", "billing/pricing", request)


@router.api_route("/api/billing/checkout/subscription", methods=["POST", "OPTIONS"])
async def api_billing_checkout_subscription_route(request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy("billing-user", "billing/checkout/subscription", request)


@router.api_route("/api/billing/checkout/credits", methods=["POST", "OPTIONS"])
async def api_billing_checkout_credits_route(request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy("billing-user", "billing/checkout/credits", request)


@router.api_route("/api/billing/checkout/api-product", methods=["POST", "OPTIONS"])
async def api_billing_checkout_api_product_route(request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy("billing-user", "billing/checkout/api-product", request)


@router.api_route("/api/billing/api-products", methods=["GET", "OPTIONS"])
async def api_billing_api_products_route(request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy_public("billing-user", "billing/api-products", request)


@router.api_route("/api/billing/api-products/me", methods=["GET", "OPTIONS"])
async def api_billing_api_products_me_route(request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy("billing-user", "billing/api-products/me", request)


@router.api_route("/api/analytics", methods=["GET", "POST", "OPTIONS"])
async def api_analytics_route(request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy("chat", "analytics", request)


@router.api_route("/api/analytics/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def api_analytics_path_route(path: str, request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy("chat", f"analytics/{path}", request)



# ============================================
# OWNER INTERNAL CATALOG → chat_service
# ============================================
@router.api_route("/owner/internal-catalog", methods=["GET", "OPTIONS"])
async def owner_internal_catalog_route(request: Request):
    """Owner internal catalog - proxied to chat_service with auth."""
    return await proxy("chat", "owner/internal-catalog", request)

@router.api_route("/api/v1/owner/internal-catalog", methods=["GET", "OPTIONS"])
async def api_v1_owner_internal_catalog_route(request: Request):
    """Owner internal catalog (v1) - proxied to chat_service with auth."""
    return await proxy("chat", "owner/internal-catalog", request)


# ============================================
# SKILLS ROUTES → chat_service
# ============================================
@router.api_route("/skills/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def skills_route(path: str, request: Request):
    """Skills API routes - proxied to chat_service with auth."""
    return await proxy("chat", f"skills/{path}", request)

@router.api_route("/api/skills/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def api_skills_route(path: str, request: Request):
    """API Skills routes - proxied to chat_service with auth."""
    return await proxy("chat", f"skills/{path}", request)

@router.api_route("/api/v1/skills/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"])
async def api_v1_skills_route(path: str, request: Request):
    """API v1 Skills routes - proxied to chat_service with auth."""
    return await proxy("chat", f"skills/{path}", request)

@router.api_route("/api/resonant-chat/analytics", methods=["GET", "POST", "OPTIONS"])
async def api_resonant_chat_analytics_route(request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy("chat", "analytics", request)


@router.api_route("/api/resonant-chat/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def api_resonant_chat_route(path: str, request: Request):
    if request.method.upper() == "OPTIONS":
        return Response(status_code=204)
    return await proxy("chat", f"resonant-chat/{path}", request)


# ============================================
# AGENT ROUTES - handled without /api/v1 prefix (added by include_router)
# ============================================
# ============================================
# AGENT ENGINE SERVICE ROUTES (CONSOLIDATED)
# ============================================
# All agent routes are consolidated here for proper ordering.
# Routes are ordered from most specific to least specific (catch-all last).
# These routes become /api/v1/agents/* when included with prefix /api/v1

# Removed duplicate route definitions - all agent routes now use the catch-all below


# API service routes removed - causing conflicts


@router.api_route("/auth/refresh", methods=["POST", "OPTIONS"])
@router.api_route("/auth/refresh/", methods=["POST", "OPTIONS"])
@router.api_route("/api/auth/refresh", methods=["POST", "OPTIONS"])
@router.api_route("/api/auth/refresh/", methods=["POST", "OPTIONS"])
async def auth_refresh_route(request: Request):
    """Token refresh route - exchanges refresh token for new access token."""
    return await proxy_public("auth", "auth/refresh", request)


@router.api_route("/auth/providers", methods=["GET", "OPTIONS"])
async def auth_providers_route(request: Request):
    """Auth providers route - SSO providers."""
    return await proxy("auth", "auth/sso/providers", request)


@router.api_route("/oauth/callback", methods=["GET", "OPTIONS"])
async def oauth_callback_route(request: Request):
    """OAuth callback route - handles OAuth provider callbacks."""
    return await proxy_public("auth", "oauth/callback", request)


@router.api_route("/owner/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def owner_auth_route(path: str, request: Request):
    """Owner authentication routes - platform owner dashboard access."""
    return await proxy("auth", f"owner/auth/{path}", request)


# Auth routes handled by main.py auth_proxy


# User routes - handled by user_routes.py (included in main.py)
# Note: /user/* endpoints are now served directly by the gateway via user_routes.py


# ============================================
# CHAT SERVICE ROUTES
# ============================================

# Analytics routes - for /api/v1/analytics calls
@router.api_route("/analytics", methods=["GET", "OPTIONS"])
async def analytics_base_route(request: Request):
    """Analytics API base route - maps to chat service analytics."""
    return await proxy("chat", "analytics", request)


@router.api_route("/analytics/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def analytics_route(path: str, request: Request):
    """Analytics API routes - maps to chat service analytics/{path}."""
    return await proxy("chat", f"analytics/{path}", request)


@router.api_route("/chat/conversations", methods=["GET", "POST", "OPTIONS"])
async def chat_conversations_base_route(request: Request):
    """Chat conversations API base route - maps to resonant-chat/conversations."""
    return await proxy("chat", "resonant-chat/conversations", request)


@router.api_route("/chat/conversations/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def chat_conversations_route(path: str, request: Request):
    """Chat conversations API routes - maps to resonant-chat/conversations/{path}."""
    return await proxy("chat", f"resonant-chat/conversations/{path}", request)


@router.api_route("/chat/send", methods=["POST", "OPTIONS"])
async def chat_send_route(request: Request):
    """Chat send message - maps to resonant-chat/message."""
    return await proxy("chat", "resonant-chat/message", request)


@router.api_route("/chat/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def chat_route(path: str, request: Request):
    """Chat Service API routes - maps to resonant-chat/{path}."""
    return await proxy("chat", f"resonant-chat/{path}", request)


@router.api_route("/resonant-chat/anchors", methods=["GET", "OPTIONS"])
async def resonant_chat_anchors_route(request: Request):
    """Compatibility route for Resonant Chat anchors.

    Frontend expects /resonant-chat/anchors but anchors live in memory service Hash Sphere.
    """
    return await proxy("memory", "memory/hash-sphere/anchors", request)


@router.api_route("/resonant-chat/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def resonant_chat_route(path: str, request: Request):
    """Direct resonant-chat routes for backwards compatibility with old frontend."""
    return await proxy("chat", f"resonant-chat/{path}", request)


# Hash Sphere routes
@router.api_route("/hash-sphere/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def hash_sphere_route(path: str, request: Request):
    """Hash Sphere API routes - routed to memory service."""
    return await proxy("memory", f"memory/hash-sphere/{path}", request)


# Agent routes - REMOVED: consolidated into single catch-all at end of file


# ML routes
@router.api_route("/ml/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def ml_route(path: str, request: Request):
    """ML Service API routes."""
    return await proxy("ml", f"ml/{path}", request)


# IDE routes
@router.api_route("/ide/tasks", methods=["GET", "POST", "OPTIONS"])
async def ide_tasks_base_route(request: Request):
    """IDE tasks base route."""
    return await proxy("ide", "api/tasks/", request)


@router.api_route("/ide/tasks/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def ide_tasks_route(path: str, request: Request):
    """IDE tasks API routes."""
    return await proxy("ide", f"api/tasks/{path}", request)


@router.api_route("/ide/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def ide_route(path: str, request: Request):
    """IDE Service API routes."""
    return await proxy("ide", f"api/ide/{path}", request)


# Code routes - routed to Code Execution microservice (public - no auth required)
@router.api_route("/code/execute", methods=["POST", "OPTIONS"])
async def code_execute_route(request: Request):
    """Code execution - routed to Code Execution microservice."""
    return await proxy_public("code-execution", "code/execute", request)

@router.api_route("/code/languages", methods=["GET", "OPTIONS"])
async def code_languages_route(request: Request):
    """Supported languages - routed to Code Execution microservice."""
    return await proxy_public("code-execution", "code/languages", request)

@router.api_route("/code/project-builder/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def code_project_builder_route(path: str, request: Request):
    """Project Builder routes - routed to Agent Engine service (project-builder)."""
    return await proxy("agents", f"project-builder/{path}", request)

# Code routes are now handled by code_routes.py router


# Terminal routes - routed to Code Execution microservice (public - no auth required)
@router.api_route("/terminal/execute", methods=["POST", "OPTIONS"])
async def terminal_execute_route(request: Request):
    """Terminal command execution - routed to Code Execution microservice."""
    return await proxy_public("code-execution", "terminal/execute", request)


# Preview routes - routed to Code Execution microservice (public - no auth required)
@router.api_route("/preview/start", methods=["POST", "OPTIONS"])
async def preview_start_route(request: Request):
    """Start preview server - routed to Code Execution microservice."""
    return await proxy_public("code-execution", "preview/start", request)

@router.api_route("/preview/stop", methods=["POST", "OPTIONS"])
async def preview_stop_route(request: Request):
    """Stop preview server - routed to Code Execution microservice."""
    return await proxy_public("code-execution", "preview/stop", request)

@router.api_route("/preview/active", methods=["GET", "OPTIONS"])
async def preview_active_route(request: Request):
    """Get active previews - routed to Code Execution microservice."""
    return await proxy_public("code-execution", "preview/active", request)


# Memory routes
@router.api_route("/memory/visualizer/{path:path}", methods=["GET", "OPTIONS"])
async def memory_visualizer_route(path: str, request: Request):
    """Memory visualizer routes - authenticated access to visualizer HTML."""
    return await proxy("memory", f"memory/visualizer/{path}", request)

@router.api_route("/memory/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def memory_route(path: str, request: Request):
    """Memory API routes - routed to memory service."""
    return await proxy("memory", f"memory/{path}", request)


# RAG routes - routed to memory service
@router.api_route("/rag/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def rag_route(path: str, request: Request):
    """RAG API routes - routed to memory service for compatibility."""
    return await proxy("memory", f"memory/rag/{path}", request)


# ============================================
# BILLING ROUTES - Split between agent_engine (ORG) and billing_service (USER)
# ============================================

# USER-level billing routes → billing_service (credits, invoices, payment methods)
@router.api_route("/billing/subscription", methods=["GET", "POST", "OPTIONS"])
async def billing_subscription_route(request: Request):
    """Get/Create subscription - USER level → billing_service."""
    return await proxy("billing-user", "billing/subscription", request)

@router.api_route("/billing/subscription/cancel", methods=["POST", "OPTIONS"])
async def billing_subscription_cancel_route(request: Request):
    """Cancel subscription - USER level → billing_service."""
    return await proxy("billing-user", "billing/subscription/cancel", request)

@router.api_route("/billing/subscription/reactivate", methods=["POST", "OPTIONS"])
async def billing_subscription_reactivate_route(request: Request):
    """Reactivate subscription - USER level → billing_service."""
    return await proxy("billing-user", "billing/subscription/reactivate", request)

@router.api_route("/billing/subscription/change-plan", methods=["POST", "OPTIONS"])
async def billing_subscription_change_plan_route(request: Request):
    """Change plan - USER level → billing_service."""
    return await proxy("billing-user", "billing/subscription/change-plan", request)

@router.api_route("/billing/credits", methods=["GET", "OPTIONS"])
async def billing_credits_route(request: Request):
    """Get credits - USER level → billing_service."""
    return await proxy("billing-user", "billing/credits", request)

@router.api_route("/billing/credits/purchase", methods=["POST", "OPTIONS"])
async def billing_credits_purchase_route(request: Request):
    """Purchase credits - USER level → billing_service."""
    return await proxy("billing-user", "billing/credits/purchase", request)

@router.api_route("/billing/credits/transactions", methods=["GET", "OPTIONS"])
async def billing_credits_transactions_route(request: Request):
    """Get credit transactions - USER level → billing_service."""
    return await proxy("billing-user", "billing/credits/transactions", request)

@router.api_route("/billing/invoices", methods=["GET", "OPTIONS"])
async def billing_invoices_list_route(request: Request):
    """List invoices - USER level → billing_service."""
    return await proxy("billing-user", "billing/invoices", request)

@router.api_route("/billing/invoices/{invoice_id}", methods=["GET", "OPTIONS"])
async def billing_invoice_get_route(invoice_id: str, request: Request):
    """Get invoice - USER level → billing_service."""
    return await proxy("billing-user", f"billing/invoices/{invoice_id}", request)

@router.api_route("/billing/invoices/{invoice_id}/pdf", methods=["GET", "OPTIONS"])
async def billing_invoice_pdf_route(invoice_id: str, request: Request):
    """Get invoice PDF - USER level → billing_service."""
    return await proxy("billing-user", f"billing/invoices/{invoice_id}/pdf", request)

@router.api_route("/billing/payment-methods", methods=["GET", "POST", "OPTIONS"])
async def billing_payment_methods_route(request: Request):
    """Payment methods - USER level → billing_service."""
    return await proxy("billing-user", "billing/payment-methods", request)

@router.api_route("/billing/payment-methods/{pm_id}", methods=["DELETE", "OPTIONS"])
async def billing_payment_method_delete_route(pm_id: str, request: Request):
    """Delete payment method - USER level → billing_service."""
    return await proxy("billing-user", f"billing/payment-methods/{pm_id}", request)

@router.api_route("/billing/payment-methods/{pm_id}/default", methods=["POST", "OPTIONS"])
async def billing_payment_method_default_route(pm_id: str, request: Request):
    """Set default payment method - USER level → billing_service."""
    return await proxy("billing-user", f"billing/payment-methods/{pm_id}/default", request)

@router.api_route("/billing/portal", methods=["POST", "OPTIONS"])
async def billing_portal_route(request: Request):
    """Stripe portal - USER level → billing_service."""
    return await proxy("billing-user", "billing/portal", request)

@router.api_route("/billing/stripe/portal", methods=["POST", "OPTIONS"])
async def billing_stripe_portal_route(request: Request):
    """Stripe portal (alias) - USER level → billing_service."""
    return await proxy("billing-user", "billing/portal", request)

@router.api_route("/billing/checkout/subscription", methods=["POST", "OPTIONS"])
async def billing_checkout_subscription_route(request: Request):
    """Subscription checkout - USER level → billing_service."""
    return await proxy("billing-user", "billing/checkout/subscription", request)

@router.api_route("/billing/checkout/credits", methods=["POST", "OPTIONS"])
async def billing_checkout_credits_route(request: Request):
    """Credits checkout - USER level → billing_service."""
    return await proxy("billing-user", "billing/checkout/credits", request)

@router.api_route("/billing/stripe/checkout", methods=["POST", "OPTIONS"])
async def billing_stripe_checkout_route(request: Request):
    """Stripe checkout - ORG level → agent_engine_service."""
    return await proxy("billing", "billing/stripe/checkout", request)

@router.api_route("/billing/checkout", methods=["POST", "OPTIONS"])
async def billing_checkout_route(request: Request):
    """Checkout session - ORG level → agent_engine_service."""
    return await proxy("billing", "billing/checkout", request)

# Stripe Webhook - NO AUTH REQUIRED (Stripe signs the request)
@router.api_route("/billing/webhook/stripe", methods=["POST"])
async def billing_webhook_stripe_route(request: Request):
    """Stripe webhook - routes to billing_service for subscription events."""
    return await proxy_public("billing-user", "billing/webhook/stripe", request)

@router.api_route("/api/billing/stripe/webhook", methods=["POST"])
async def api_billing_stripe_webhook_route(request: Request):
    """Stripe webhook (matches Stripe dashboard config) - routes to billing_service."""
    return await proxy_public("billing-user", "billing/webhook/stripe", request)

@router.api_route("/webhook/stripe", methods=["POST"])
async def webhook_stripe_route(request: Request):
    """Stripe webhook (alias) - routes to agent_engine_service."""
    return await proxy_public("billing", "billing/webhook/stripe", request)

# Usage routes - routed to billing_service (USER-level usage metrics)
# NOTE: These MUST be defined BEFORE the catch-all /billing/{path:path} route

# ============================================
# PRICING ROUTES (PUBLIC - no auth required)
# ============================================
@router.api_route("/billing/pricing", methods=["GET", "OPTIONS"])
async def billing_pricing_route(request: Request):
    """Pricing config - PUBLIC → billing_service."""
    return await proxy("billing-user", "billing/pricing", request)

@router.api_route("/billing/pricing/plans", methods=["GET", "OPTIONS"])
async def billing_pricing_plans_route(request: Request):
    """All plans - PUBLIC → billing_service."""
    return await proxy("billing-user", "billing/pricing/plans", request)

@router.api_route("/billing/pricing/plans/{plan_id}", methods=["GET", "OPTIONS"])
async def billing_pricing_plan_route(plan_id: str, request: Request):
    """Specific plan - PUBLIC → billing_service."""
    return await proxy("billing-user", f"billing/pricing/plans/{plan_id}", request)

@router.api_route("/billing/pricing/credit-packs", methods=["GET", "OPTIONS"])
async def billing_pricing_credit_packs_route(request: Request):
    """Credit packs - PUBLIC → billing_service."""
    return await proxy("billing-user", "billing/pricing/credit-packs", request)

@router.api_route("/billing/pricing/credit-costs", methods=["GET", "OPTIONS"])
async def billing_pricing_credit_costs_route(request: Request):
    """Credit costs - PUBLIC → billing_service."""
    return await proxy("billing-user", "billing/pricing/credit-costs", request)

# Alias routes for frontend compatibility
@router.api_route("/billing/packs", methods=["GET", "OPTIONS"])
async def billing_packs_alias_route(request: Request):
    """Credit packs alias - maps to /billing/pricing/credit-packs."""
    return await proxy("billing-user", "billing/pricing/credit-packs", request)

@router.api_route("/billing/plans", methods=["GET", "OPTIONS"])
async def billing_plans_alias_route(request: Request):
    """Plans alias - maps to /billing/pricing/plans."""
    return await proxy("billing-user", "billing/pricing/plans", request)

@router.api_route("/billing/token-packs", methods=["GET", "OPTIONS"])
async def billing_token_packs_alias_route(request: Request):
    """Token packs alias - maps to /billing/pricing/credit-packs."""
    return await proxy("billing-user", "billing/pricing/credit-packs", request)

@router.api_route("/billing/overview", methods=["GET", "OPTIONS"])
async def billing_overview_route(request: Request):
    """Billing overview - maps to dashboard/me."""
    return await proxy("billing-user", "dashboard/me", request)

@router.api_route("/billing/history", methods=["GET", "OPTIONS"])
async def billing_history_route(request: Request):
    """Billing history - maps to credits/transactions."""
    return await proxy("billing-user", "billing/credits/transactions", request)

# Economic State routes
@router.api_route("/billing/economic-state/me", methods=["GET", "OPTIONS"])
async def billing_economic_state_me_route(request: Request):
    """Economic state for current user → billing_service."""
    return await proxy("billing-user", "economic-state/me", request)

@router.api_route("/billing/economic-state/me/check-credits", methods=["POST", "OPTIONS"])
async def billing_economic_state_check_credits_route(request: Request):
    """Check credits → billing_service."""
    return await proxy("billing-user", "economic-state/me/check-credits", request)

@router.api_route("/billing/economic-state/me/check-limit", methods=["POST", "OPTIONS"])
async def billing_economic_state_check_limit_route(request: Request):
    """Check limit → billing_service."""
    return await proxy("billing-user", "economic-state/me/check-limit", request)

# ============================================
# USER BILLING ROUTES
# ============================================
@router.api_route("/billing/usage/summary", methods=["GET", "OPTIONS"])
async def billing_usage_summary_route(request: Request):
    """Usage summary - USER level → billing_service."""
    return await proxy("billing-user", "billing/usage/summary", request)

@router.api_route("/billing/usage/metrics", methods=["GET", "OPTIONS"])
async def billing_usage_metrics_route(request: Request):
    """Usage metrics - USER level → billing_service."""
    return await proxy("billing-user", "billing/usage/metrics", request)

@router.api_route("/billing/usage/breakdown", methods=["GET", "OPTIONS"])
async def billing_usage_breakdown_route(request: Request):
    """Usage breakdown by service - USER level → billing_service."""
    return await proxy("billing-user", "billing/usage/breakdown", request)

@router.api_route("/billing/usage/tokens/history", methods=["GET", "OPTIONS"])
async def billing_usage_tokens_history_route(request: Request):
    """Token usage history - USER level → billing_service."""
    return await proxy("billing-user", "billing/usage/tokens/history", request)

@router.api_route("/billing/dashboard/me", methods=["GET", "OPTIONS"])
async def billing_dashboard_me_route(request: Request):
    """Dashboard data for current user → billing_service."""
    user_id = request.state.user_id if hasattr(request.state, 'user_id') else None
    if user_id:
        return await proxy("billing-user", f"dashboard/{user_id}", request)
    return await proxy("billing-user", "dashboard/me", request)

@router.api_route("/billing/dashboard/me/breakdown", methods=["GET", "OPTIONS"])
async def billing_dashboard_breakdown_route(request: Request):
    """Dashboard breakdown for current user → billing_service."""
    user_id = request.state.user_id if hasattr(request.state, 'user_id') else None
    if user_id:
        return await proxy("billing-user", f"dashboard/{user_id}/breakdown", request)
    return await proxy("billing-user", "dashboard/me/breakdown", request)

@router.api_route("/billing/dashboard/me/usage-chart", methods=["GET", "OPTIONS"])
async def billing_dashboard_chart_route(request: Request):
    """Dashboard usage chart for current user → billing_service."""
    user_id = request.state.user_id if hasattr(request.state, 'user_id') else None
    if user_id:
        return await proxy("billing-user", f"dashboard/{user_id}/usage-chart", request)
    return await proxy("billing-user", "dashboard/me/usage-chart", request)

@router.api_route("/usage/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def usage_route(path: str, request: Request):
    """Usage API routes - routed to billing_service for per-user metrics."""
    return await proxy("billing-user", f"billing/usage/{path}", request)



# Policies routes - handled by policies_routes.py (included in main.py)
# Note: /policies/* endpoints are now served directly by the gateway via policies_routes.py


# AI audit compatibility routes - routed to blockchain service
@router.api_route("/audit/ai-audit/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def ai_audit_route(path: str, request: Request):
    """AI Audit API routes - routed to blockchain service compatibility endpoints."""
    return await proxy("blockchain", f"blockchain/ai-audit/{path}", request)


# Public Hash Sphere token endpoint
@router.api_route("/public/hash-sphere/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def public_hash_sphere_route(path: str, request: Request):
    """Public Hash Sphere routes (no auth required)."""
    return await proxy_public("memory", f"public/hash-sphere/{path}", request)


# Public signup endpoint (no auth required)
@router.api_route("/public/signup", methods=["POST", "OPTIONS"])
async def public_signup_route(request: Request):
    """Public signup route - proxies to auth service register endpoint."""
    return await proxy_public("auth", "auth/register", request)


# User routes - routed to auth service (user management, API keys, trial status)
@router.api_route("/user/profile", methods=["GET", "POST", "PUT", "OPTIONS"])
async def user_profile_route(request: Request):
    """User profile route - routed to user service."""
    return await proxy("user", "users/me", request)


@router.api_route("/user/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def user_route(path: str, request: Request):
    """User API routes - routed to auth service."""
    return await proxy("auth", f"auth/user/{path}", request)


# Users routes - handled by user_routes.py (included in main.py)
# Note: /users/* endpoints are now served directly by the gateway via user_routes.py


# Orgs routes - handled by orgs_routes.py (included in main.py)
# Note: /orgs/* endpoints are now served directly by the gateway via orgs_routes.py


# Agent Teams routes - routed to agent engine service
@router.api_route("/agent-teams", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def agent_teams_base_route(request: Request):
    """Agent Teams API base route - routed to agent engine service."""
    return await proxy("agents", "agents/teams", request)

@router.api_route("/agent-teams/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def agent_teams_route(path: str, request: Request):
    """Agent Teams API routes - routed to agent engine service."""
    return await proxy("agents", f"agents/teams/{path}", request)


# Settings routes - handled by settings_routes.py (included in main.py)
# Note: Settings endpoints are now served directly by the gateway via settings_routes.py


# Admin routes - handled by admin_routes.py (included in main.py)
# Note: Admin endpoints are now served directly by the gateway via admin_routes.py


# Git routes - handled by git_routes.py (included in main.py)
# Note: Git endpoints are now served directly by the gateway via git_routes.py


# GitHub routes - handled by git_routes.py (included in main.py)
# Note: GitHub endpoints are now served directly by the gateway via git_routes.py


# ============================================
# WEBHOOK ROUTES (PUBLIC - no auth required for incoming webhooks)
# ============================================
# External services (Discord, GitHub, etc.) POST to these endpoints.
# MUST be defined BEFORE the /agents catch-all.

@router.api_route("/webhooks/agent/{agent_id}/trigger", methods=["POST", "OPTIONS"])
async def webhook_agent_trigger_route(agent_id: str, request: Request):
    """Public webhook trigger — external services call this to trigger an agent."""
    return await proxy_public("agents", f"webhooks/agent/{agent_id}/trigger", request)

@router.api_route("/webhooks/github/{trigger_id}", methods=["POST", "OPTIONS"])
async def webhook_github_trigger_route(trigger_id: str, request: Request):
    """Public GitHub webhook trigger."""
    return await proxy_public("agents", f"webhooks/github/{trigger_id}", request)

# Authenticated webhook CRUD (create, list, delete, toggle)
@router.api_route("/webhooks/agent/{agent_id}/create", methods=["POST", "OPTIONS"])
async def webhook_create_route(agent_id: str, request: Request):
    """Create a webhook trigger for an agent (authenticated)."""
    return await proxy("agents", f"webhooks/agent/{agent_id}/create", request)

@router.api_route("/webhooks/agent/{agent_id}/list", methods=["GET", "OPTIONS"])
async def webhook_list_route(agent_id: str, request: Request):
    """List webhook triggers for an agent (authenticated)."""
    return await proxy("agents", f"webhooks/agent/{agent_id}/list", request)

@router.api_route("/webhooks/trigger/{trigger_id}", methods=["DELETE", "OPTIONS"])
async def webhook_delete_route(trigger_id: str, request: Request):
    """Delete a webhook trigger (authenticated)."""
    return await proxy("agents", f"webhooks/trigger/{trigger_id}", request)

@router.api_route("/webhooks/trigger/{trigger_id}/toggle", methods=["PATCH", "OPTIONS"])
async def webhook_toggle_route(trigger_id: str, request: Request):
    """Toggle a webhook trigger (authenticated)."""
    return await proxy("agents", f"webhooks/trigger/{trigger_id}/toggle", request)

@router.api_route("/webhooks/user/list", methods=["GET", "OPTIONS"])
async def webhook_user_list_route(request: Request):
    """List all webhook triggers for the authenticated user."""
    return await proxy("agents", "webhooks/user/list", request)


# ============================================
# OPENCLAW INTEGRATION ROUTES
# ============================================
# Proxied to openclaw_service (standalone, isolated microservice)

@router.api_route("/openclaw/health", methods=["GET"])
async def openclaw_health_route(request: Request):
    """OpenClaw service health check."""
    return await proxy("openclaw", "health", request)

@router.api_route("/openclaw/status", methods=["GET"])
async def openclaw_status_route(request: Request):
    """Quick connection status for frontend card."""
    return await proxy("openclaw", "status", request)

@router.api_route("/openclaw/connections", methods=["GET"])
async def openclaw_list_connections_route(request: Request):
    """List user's OpenClaw connections."""
    return await proxy("openclaw", "connections", request)

@router.api_route("/openclaw/connections", methods=["POST", "OPTIONS"])
async def openclaw_create_connection_route(request: Request):
    """Create a new OpenClaw connection."""
    return await proxy("openclaw", "connections", request)

@router.api_route("/openclaw/connections/{trigger_id}", methods=["DELETE", "OPTIONS"])
async def openclaw_delete_connection_route(trigger_id: str, request: Request):
    """Delete an OpenClaw connection."""
    return await proxy("openclaw", f"connections/{trigger_id}", request)

@router.api_route("/openclaw/connections/{trigger_id}/pause", methods=["POST", "OPTIONS"])
async def openclaw_pause_connection_route(trigger_id: str, request: Request):
    """Pause an OpenClaw connection."""
    return await proxy("openclaw", f"connections/{trigger_id}/pause", request)

@router.api_route("/openclaw/connections/{trigger_id}/resume", methods=["POST", "OPTIONS"])
async def openclaw_resume_connection_route(trigger_id: str, request: Request):
    """Resume an OpenClaw connection."""
    return await proxy("openclaw", f"connections/{trigger_id}/resume", request)

@router.api_route("/openclaw/manifest", methods=["GET"])
async def openclaw_manifest_route(request: Request):
    """Get ClawHub skill manifest (auth required)."""
    return await proxy("openclaw", "manifest", request)

@router.api_route("/openclaw/setup-guide", methods=["GET"])
async def openclaw_setup_guide_route(request: Request):
    """Get OpenClaw setup guide (auth required)."""
    return await proxy("openclaw", "setup-guide", request)

@router.api_route("/openclaw/relay/{agent_id}", methods=["POST", "OPTIONS"])
async def openclaw_relay_route(agent_id: str, request: Request):
    """Relay OpenClaw event to agent webhook (auth required)."""
    return await proxy("openclaw", f"relay/{agent_id}", request)

# -- OpenClaw Federation: Agent Registration & Heartbeat --

@router.api_route("/openclaw/agents/register", methods=["POST", "OPTIONS"])
async def openclaw_register_agent_route(request: Request):
    """Register an OpenClaw agent (creates on platform + DSID + RARA)."""
    return await proxy("openclaw", "agents/register", request)

@router.api_route("/openclaw/agents/heartbeat", methods=["POST", "OPTIONS"])
async def openclaw_heartbeat_route(request: Request):
    """Heartbeat from OpenClaw agent running on user hardware."""
    return await proxy("openclaw", "agents/heartbeat", request)

@router.api_route("/openclaw/agents/openclaw", methods=["GET"])
async def openclaw_list_agents_route(request: Request):
    """List user's OpenClaw agents with connection status."""
    return await proxy("openclaw", "agents/openclaw", request)

# -- OpenClaw Federation: Memory Bridge --

@router.api_route("/openclaw/memory/ingest", methods=["POST", "OPTIONS"])
async def openclaw_memory_ingest_route(request: Request):
    """Ingest memory into Hash Sphere from OpenClaw agent."""
    return await proxy("openclaw", "memory/ingest", request)

@router.api_route("/openclaw/memory/query", methods=["POST", "OPTIONS"])
async def openclaw_memory_query_route(request: Request):
    """Query Hash Sphere memories for an OpenClaw agent."""
    return await proxy("openclaw", "memory/query", request)

# -- OpenClaw Federation: Skills --

@router.api_route("/openclaw/skills/available", methods=["GET"])
async def openclaw_skills_available_route(request: Request):
    """List platform skills available to OpenClaw agents."""
    return await proxy("openclaw", "skills/available", request)

@router.api_route("/openclaw/skills/execute", methods=["POST", "OPTIONS"])
async def openclaw_skills_execute_route(request: Request):
    """Execute a platform skill on behalf of OpenClaw agent."""
    return await proxy("openclaw", "skills/execute", request)

@router.api_route("/openclaw/skills/import", methods=["POST", "OPTIONS"])
async def openclaw_skills_import_route(request: Request):
    """Import a custom skill from OpenClaw agent."""
    return await proxy("openclaw", "skills/import", request)

# -- OpenClaw Federation: Governance --

@router.api_route("/openclaw/governance/{agent_id}", methods=["GET"])
async def openclaw_governance_status_route(agent_id: str, request: Request):
    """Governance status for an OpenClaw agent."""
    return await proxy("openclaw", f"governance/{agent_id}", request)

@router.api_route("/openclaw/governance/enroll", methods=["POST", "OPTIONS"])
async def openclaw_governance_enroll_route(request: Request):
    """Enroll OpenClaw agent in RARA governance."""
    return await proxy("openclaw", "governance/enroll", request)

# -- OpenClaw Federation: Marketplace --

@router.api_route("/openclaw/marketplace/list", methods=["POST", "OPTIONS"])
async def openclaw_marketplace_list_route(request: Request):
    """List an OpenClaw agent on the marketplace."""
    return await proxy("openclaw", "marketplace/list", request)


# ============================================
# LOCAL LLM TUNNEL ROUTES
# ============================================

async def _resolve_user_id(request: Request) -> str | None:
    """Extract user_id from x-user-id header, request.state, or JWT Authorization header."""
    uid = request.headers.get("x-user-id") or getattr(request.state, "user_id", None)
    if uid:
        return uid
    auth_header = request.headers.get("authorization", "")
    if auth_header.startswith("Bearer "):
        from .auth_middleware import verify_token_for_ws
        uid = await verify_token_for_ws(auth_header[7:])
    return uid

@router.api_route("/local-llm/tunnel/status", methods=["GET"])
async def local_llm_tunnel_status(request: Request):
    """Check if user has an active local LLM tunnel."""
    from .services.local_llm_tunnel import tunnel_manager
    user_id = await _resolve_user_id(request)
    if not user_id:
        return JSONResponse({"connected": False, "error": "Not authenticated"}, status_code=401)
    return JSONResponse(tunnel_manager.status(user_id))

@router.api_route("/local-llm/tunnel/completions", methods=["POST", "OPTIONS"])
async def local_llm_tunnel_completions(request: Request):
    """Proxy a chat completion through user's local LLM tunnel.
    
    Called by chat_service (internal) or directly by authenticated user.
    Body: { messages, model, temperature, max_tokens, user_id (internal only) }
    """
    from .services.local_llm_tunnel import tunnel_manager
    import json as _json
    
    body = await request.json()
    user_id = await _resolve_user_id(request)
    if not user_id:
        return JSONResponse({"error": "Not authenticated"}, status_code=401)
    
    if not tunnel_manager.has_tunnel(user_id):
        return JSONResponse({"error": "No local LLM tunnel active. Open ResonantGenesis in your browser to connect."}, status_code=503)
    
    try:
        result = await tunnel_manager.proxy_completion(
            user_id=user_id,
            messages=body.get("messages", []),
            model=body.get("model", "llama3.1:8b"),
            temperature=body.get("temperature", 0.7),
            max_tokens=body.get("max_tokens"),
            stream=body.get("stream", False),
        )
        if "error" in result:
            return JSONResponse({"error": result["error"]}, status_code=502)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=502)


# ============================================
# DISCORD INTEGRATION ROUTES
# ============================================
# Authenticated CRUD for Discord connections (users manage via platform UI)

@router.api_route("/discord/connections", methods=["POST", "OPTIONS"])
async def discord_create_connection_route(request: Request):
    """Create a Discord guild → agent connection."""
    return await proxy("agents", "discord/connections", request)

@router.api_route("/discord/connections", methods=["GET"])
async def discord_list_connections_route(request: Request):
    """List user's Discord connections."""
    return await proxy("agents", "discord/connections", request)

@router.api_route("/discord/connections/{connection_id}", methods=["GET"])
async def discord_get_connection_route(connection_id: str, request: Request):
    """Get a single Discord connection."""
    return await proxy("agents", f"discord/connections/{connection_id}", request)

@router.api_route("/discord/connections/{connection_id}", methods=["PATCH", "OPTIONS"])
async def discord_update_connection_route(connection_id: str, request: Request):
    """Update a Discord connection."""
    return await proxy("agents", f"discord/connections/{connection_id}", request)

@router.api_route("/discord/connections/{connection_id}", methods=["DELETE", "OPTIONS"])
async def discord_delete_connection_route(connection_id: str, request: Request):
    """Delete a Discord connection."""
    return await proxy("agents", f"discord/connections/{connection_id}", request)

@router.api_route("/discord/invite-url", methods=["GET"])
async def discord_invite_url_route(request: Request):
    """Get the platform bot invite URL."""
    return await proxy("agents", "discord/invite-url", request)


# ============================================
# AGENT ENGINE SERVICE ROUTES (SINGLE CATCH-ALL)
# ============================================
# All agent routes are handled by this single catch-all route.
# This ensures proper routing without duplicate definitions.
# The agent_engine_service handles all /agents/* endpoints internally.


@router.get("/agents/openapi.json")
async def agent_engine_openapi_proxy(request: Request):
    return await proxy("agents", "openapi.json", request)


@router.get("/agents/docs")
async def agent_engine_docs_proxy(request: Request):
    return await proxy("agents", "docs", request)


@router.get("/agents/redoc")
async def agent_engine_redoc_proxy(request: Request):
    return await proxy("agents", "redoc", request)

@router.api_route("/agents/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def agent_engine_proxy(path: str, request: Request):
    """Proxy all agent requests to agent_engine_service.
    
    This is a production-grade catch-all that forwards all /agents/* requests
    to the agent_engine_service. The service handles routing internally.
    
    Security: Authentication is handled by AuthMiddleware before this route.
    """
    # Construct target path with trailing slash for base route
    if not path:
        target_path = "agents/"
    else:
        target_path = f"agents/{path}"
    
    return await proxy("agents", target_path, request)


@router.api_route("/agents", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def agent_engine_base_proxy(request: Request):
    """Proxy base /agents requests to agent_engine_service.
    
    This handles the base /agents endpoint (list/create agents).
    Defined separately to ensure it matches before the catch-all.
    
    Security: Authentication is handled by AuthMiddleware before this route.
    """
    return await proxy("agents", "agents/", request)


@router.api_route("/execution/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def execution_route(path: str, request: Request):
    return await proxy("agents", f"execution/{path}", request)


@router.api_route("/execution", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def execution_base_route(request: Request):
    return await proxy("agents", "execution", request)


@router.api_route("/negotiations/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def negotiations_route(path: str, request: Request):
    return await proxy("agents", f"negotiations/{path}", request)


@router.api_route("/negotiations", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def negotiations_base_route(request: Request):
    return await proxy("agents", "negotiations", request)


# ============================================
# API/V1 ROUTES - IDE AI and Code Operations
# ============================================
# These routes support the frontend IDE AI services (IntentClassifier, ContextAggregator, SmartExecutor)

@router.api_route("/ai/classify-intent", methods=["POST", "OPTIONS"])
async def api_v1_ai_classify_intent_route(request: Request):
    """AI intent classification - routed to LLM service."""
    return await proxy("llm", "llm/ai/classify-intent", request)

# AI routes handled above - duplicates removed

@router.api_route("code/structure", methods=["GET", "OPTIONS"])
async def api_v1_code_structure_route(request: Request):
    """Get project structure - routed to IDE service."""
    return await proxy("ide", "api/ide/project/structure", request)

@router.api_route("code/dependencies", methods=["GET", "OPTIONS"])
async def api_v1_code_dependencies_route(request: Request):
    """Get project dependencies - routed to IDE service."""
    return await proxy("ide", "api/ide/project/dependencies", request)

@router.api_route("code/file", methods=["GET", "POST", "DELETE", "OPTIONS"])
async def api_v1_code_file_route(request: Request):
    """File operations - routed to IDE service."""
    return await proxy("ide", "api/ide/file", request)

@router.api_route("code/modify", methods=["POST", "OPTIONS"])
async def api_v1_code_modify_route(request: Request):
    """Modify file - routed to IDE service."""
    return await proxy("ide", "api/ide/file/modify", request)

@router.api_route("code/search", methods=["GET", "OPTIONS"])
async def api_v1_code_search_route(request: Request):
    """Search code - routed to IDE service."""
    return await proxy("ide", "api/ide/search", request)

@router.api_route("code/verify", methods=["POST", "OPTIONS"])
async def api_v1_code_verify_route(request: Request):
    """Verify code changes - routed to IDE service."""
    return await proxy("ide", "api/ide/verify", request)

# Code routes are now handled by code_routes.py router


# AI routes - routed to LLM service
@router.api_route("/ai/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def ai_route(path: str, request: Request):
    """AI API routes - routed to LLM service."""
    return await proxy("llm", f"llm/ai/{path}", request)


# ============================================
# AUTONOMY SERVICE ROUTES
# ============================================

@router.api_route("/autonomy/status", methods=["GET", "OPTIONS"])
async def autonomy_status_route(request: Request):
    """Autonomy status - routed to agent_engine_service."""
    return await proxy("agents", "autonomy/status", request)

@router.api_route("/autonomy/start", methods=["POST", "OPTIONS"])
async def autonomy_start_route(request: Request):
    """Start autonomy - routed to agent_engine_service."""
    return await proxy("agents", "autonomy/start", request)

@router.api_route("/autonomy/stop", methods=["POST", "OPTIONS"])
async def autonomy_stop_route(request: Request):
    """Stop autonomy - routed to agent_engine_service."""
    return await proxy("agents", "autonomy/stop", request)

@router.api_route("/autonomy/stats", methods=["GET", "OPTIONS"])
async def autonomy_stats_route(request: Request):
    """Autonomy stats - routed to agent_engine_service."""
    return await proxy("agents", "autonomy/stats", request)

@router.api_route("/autonomy/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def autonomy_route(path: str, request: Request):
    """Autonomy API routes - routed to agent_engine_service."""
    return await proxy("agents", f"autonomy/{path}", request)


@router.api_route("/wallets/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def wallets_route(path: str, request: Request):
    """Wallet API routes - routed to agent_engine_service."""
    return await proxy("agents", f"wallets/{path}", request)


@router.api_route("/wallets", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def wallets_base_route(request: Request):
    """Wallet base route - routed to agent_engine_service."""
    return await proxy("agents", "wallets", request)


@router.api_route("/metrics", methods=["GET", "OPTIONS"])
async def metrics_route(request: Request):
    """Platform metrics endpoint used by AgentOS UI."""
    return await proxy("agents", "agents/metrics", request)


# AI Agent routes - routed to ED service
@router.api_route("/ai-agent/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def ai_agent_route(path: str, request: Request):
    """AI Agent API routes - routed to ED service."""
    return await proxy("ed", f"ed/ai-agent/{path}", request)


# Compliance routes - routed to blockchain service
@router.api_route("/compliance/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def compliance_route(path: str, request: Request):
    """Compliance API routes - routed to blockchain service."""
    return await proxy("blockchain", f"blockchain/compliance/{path}", request)


# Audit routes - /audit/logs, /audit/stats, /audit/anchor/* are handled in main.py
# Only proxy /audit/blockchain/* to blockchain service
@router.api_route("/audit/blockchain/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def audit_blockchain_route(path: str, request: Request):
    """Audit blockchain API routes - routed to blockchain service."""
    return await proxy("blockchain", f"blockchain/audit/{path}", request)


# Finance routes - handled by finance_routes.py (included in main.py)
# Note: Finance endpoints are now served directly by the gateway via finance_routes.py


# Predict route - handled by predictions_routes.py (included in main.py)
# Note: /predict endpoint is now served directly by the gateway via predictions_routes.py


# Workflow routes
@router.api_route("/workflow/health", methods=["GET", "OPTIONS"])
async def workflow_health_route(request: Request):
    """Workflow Service health check."""
    return await proxy("workflow", "health", request)

@router.api_route("/workflow/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def workflow_route(path: str, request: Request):
    """Workflow Service API routes."""
    return await proxy("workflow", f"workflow/{path}", request)


# Marketplace routes
@router.api_route("/marketplace/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def marketplace_route(path: str, request: Request):
    """Marketplace API routes."""
    return await proxy("marketplace", f"marketplace/{path}", request)


# ============================================
# TERMINAL ROUTES - handled by terminal_routes.py (included in main.py)
# ============================================
# Note: /terminal/session/* endpoints are now served directly by the gateway via terminal_routes.py


# ============================================
# API/IDE ROUTES - IDE debugger and terminal
# ============================================

@router.api_route("/api/ide/debugger/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def api_ide_debugger_route(path: str, request: Request):
    """IDE Debugger API routes."""
    return await proxy("ide", f"api/ide/debugger/{path}", request)


@router.api_route("/api/ide/terminal/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def api_ide_terminal_route(path: str, request: Request):
    """IDE Terminal API routes."""
    return await proxy("ide", f"api/ide/terminal/{path}", request)


# ============================================
# PREDICTIONS ROUTES - handled by predictions_routes.py (included in main.py)
# ============================================
# Note: /predictions/* endpoints are now served directly by the gateway


# ============================================
# ANCHORS ROUTES - handled by anchors_routes.py (included in main.py)
# ============================================
# Note: /anchors/* endpoints are now served directly by the gateway via anchors_routes.py


# ============================================
# RESONANT GENESIS V8 ROUTES - OWNER ONLY
# ============================================
# These routes serve the ResonantGenesis V8 frontend and API only to platform_owner users

# V8 API routes - proxy to v8_api_service (OWNER ONLY)
# Note: Frontend calls /v8/api/predict, nginx proxies /api/v1/ to gateway

async def verify_owner_token(request: Request) -> bool:
    """Verify owner token from Authorization header."""
    import httpx
    import os
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return False
    token = auth_header.split(" ", 1)[1].strip()
    if not token:
        return False
    
    # Verify with owner auth service
    auth_url = os.getenv("AUTH_URL", "http://auth_service:8000")
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{auth_url}/owner/auth/validate",
                headers={"Authorization": f"Bearer {token}"},
                timeout=5.0
            )
            if resp.status_code == 200:
                data = resp.json()
                return data.get("valid", False) and data.get("role") in ("owner", "platform_owner")
    except Exception:
        pass
    return False

@router.api_route("/v8/api/predict", methods=["POST", "OPTIONS"])
async def v8_api_predict_route(request: Request):
    """V8 Hash prediction API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    
    # Check owner token authentication (also accepts X-Dev-Token and cookie-based auth)
    if not await verify_v8_admin_access(request):
        return JSONResponse(
            status_code=403,
            content={"error": "Access denied. Platform owner authentication required."}
        )
    
    return await proxy("v8-api", "api/predict", request)


@router.api_route("/api/v1/v8/api/anchor", methods=["POST", "OPTIONS"])
async def v8_api_anchor_route(request: Request):
    """V8 Add anchor API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    
    if not await verify_owner_token(request):
        return JSONResponse(
            status_code=403,
            content={"error": "Access denied. Platform owner authentication required."}
        )
    
    return await proxy("v8-api", "api/anchor", request)


@router.api_route("/v8/api/retrain", methods=["POST", "OPTIONS"])
async def v8_api_retrain_route(request: Request):
    """V8 Retrain API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    
    if not await verify_owner_token(request):
        return JSONResponse(
            status_code=403,
            content={"error": "Access denied. Platform owner authentication required."}
        )
    
    return await proxy("v8-api", "api/retrain", request)


@router.api_route("/v8/api/forbidden", methods=["POST", "OPTIONS"])
async def v8_api_forbidden_route(request: Request):
    """V8 Forbidden words API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    
    if not await verify_owner_token(request):
        return JSONResponse(
            status_code=403,
            content={"error": "Access denied. Platform owner authentication required."}
        )
    
    return await proxy("v8-api", "api/forbidden", request)


# ============================================
# V8 ADMIN API ROUTES (OWNER ONLY)
# These MUST be defined BEFORE the /v8/{path:path} catch-all
# ============================================

async def verify_v8_admin_access(request: Request) -> bool:
    """Verify V8 admin access via owner JWT, X-Dev-Token, or session cookie.
    Accepts three auth methods:
      1. Authorization: Bearer <owner_jwt> → validated via auth_service
      2. X-Dev-Token header matching V8_DEV_TOKEN env var
      3. Cookie/session where auth middleware already set request.state.role
    """
    import os

    # Method 1: Owner JWT Bearer token
    if await verify_owner_token(request):
        return True

    # Method 2: X-Dev-Token header (used by V8ControlPanel)
    dev_token = request.headers.get("x-dev-token", "")
    expected = os.getenv("V8_DEV_TOKEN", "LouieArt")
    if dev_token and dev_token == expected:
        return True

    # Method 3: Cookie-based session (auth middleware sets request.state.role)
    role = getattr(request.state, 'role', None)
    if role in ('owner', 'platform_owner', 'admin'):
        return True

    # Method 4: rg_access_token cookie (V8AdminPanel sends credentials:include)
    # Middleware skips /api/v1/v8/api/ paths, so we validate cookie directly
    import httpx
    cookie_token = request.cookies.get("rg_access_token", "")
    if cookie_token:
        auth_url = os.getenv("AUTH_URL", "http://auth_service:8000")
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{auth_url}/auth/verify",
                    headers={"Authorization": f"Bearer {cookie_token}"},
                    timeout=5.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("role") in ("owner", "platform_owner", "admin"):
                        return True
        except Exception:
            pass

    return False


async def proxy_v8_admin(path: str, request: Request):
    """Proxy V8 admin requests with X-Gateway-Secret header."""
    import httpx
    import os
    from starlette.responses import Response
    
    target = f"http://v8_api_service:8080/{path}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "x-dev-token")}
    headers["X-Gateway-Secret"] = os.getenv("GATEWAY_SECRET", "v8-gw-internal-2026")
    
    body = await request.body()
    
    try:
        async with httpx.AsyncClient(timeout=900.0) as client:
            response = await client.request(
                method=request.method,
                url=target,
                headers=headers,
                content=body,
                params=request.query_params,
            )
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers={k: v for k, v in response.headers.items() if k.lower() not in ["transfer-encoding", "content-encoding"]},
            )
    except Exception as e:
        return Response(content=json.dumps({"error": str(e)}).encode(), status_code=502)


@router.api_route("/v8/api/admin/status", methods=["GET", "OPTIONS"])
async def v8_admin_status_route(request: Request):
    """V8 Admin Status API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/status", request)


@router.api_route("/v8/api/admin/forbidden", methods=["GET", "POST", "OPTIONS"])
async def v8_admin_forbidden_route(request: Request):
    """V8 Admin Forbidden Words API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/forbidden", request)


@router.api_route("/v8/api/admin/vocab", methods=["GET", "OPTIONS"])
async def v8_admin_vocab_route(request: Request):
    """V8 Admin Vocabulary API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/vocab", request)


@router.api_route("/v8/api/admin/anchors", methods=["GET", "POST", "OPTIONS"])
async def v8_admin_anchors_route(request: Request):
    """V8 Admin Anchors API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/anchors", request)


@router.api_route("/v8/api/admin/formula", methods=["GET", "POST", "OPTIONS"])
async def v8_admin_formula_route(request: Request):
    """V8 Admin Formula API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/formula", request)


@router.api_route("/v8/api/admin/corpus", methods=["GET", "POST", "OPTIONS"])
async def v8_admin_corpus_route(request: Request):
    """V8 Admin Corpus API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/corpus", request)


@router.api_route("/v8/api/admin/training/start", methods=["POST", "OPTIONS"])
async def v8_admin_training_start_route(request: Request):
    """V8 Admin Training Start API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/training/start", request)


@router.api_route("/v8/api/admin/training/status", methods=["GET", "OPTIONS"])
async def v8_admin_training_status_route(request: Request):
    """V8 Admin Training Status API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/training/status", request)


@router.api_route("/v8/api/admin/models", methods=["GET", "OPTIONS"])
async def v8_admin_models_route(request: Request):
    """V8 Admin Models API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/models", request)


@router.api_route("/v8/api/admin/models/activate", methods=["POST", "OPTIONS"])
async def v8_admin_models_activate_route(request: Request):
    """V8 Admin Model Activate API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/models/activate", request)


@router.api_route("/v8/api/admin/predictions", methods=["GET", "OPTIONS"])
async def v8_admin_predictions_route(request: Request):
    """V8 Admin Predictions API - OWNER ONLY access."""
    from starlette.responses import JSONResponse
    if not await verify_v8_admin_access(request):
        return JSONResponse(status_code=403, content={"error": "Access denied. Platform owner authentication required."})
    return await proxy_v8_admin("api/admin/predictions", request)


# V8 Frontend static files (OWNER ONLY) - CATCH-ALL must be AFTER specific routes
@router.api_route("/v8/{path:path}", methods=["GET", "OPTIONS"])
async def v8_frontend_route(path: str, request: Request):
    """ResonantGenesis V8 frontend - OWNER ONLY access.
    
    Serves static files from /var/www/resonantgenesis_v8 only to authenticated
    platform_owner users.
    """
    from starlette.responses import JSONResponse, FileResponse
    import os
    
    # Check authentication - must have platform_owner role
    user_role = getattr(request.state, 'role', None)
    if user_role != 'platform_owner':
        return JSONResponse(
            status_code=403,
            content={"error": "Access denied. Platform owner authentication required."}
        )
    
    # Serve static files from /var/www/resonantgenesis_v8
    base_path = "/var/www/resonantgenesis_v8"
    
    # Default to index.html if no path
    if not path or path == "/":
        path = "index.html"
    
    file_path = os.path.join(base_path, path)
    
    # Security: prevent directory traversal
    real_base = os.path.realpath(base_path)
    real_file = os.path.realpath(file_path)
    if not real_file.startswith(real_base):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    
    if os.path.isfile(real_file):
        return FileResponse(real_file)
    
    # Try index.html for SPA routing
    index_path = os.path.join(base_path, "index.html")
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    
    return JSONResponse(status_code=404, content={"error": "File not found"})


@router.api_route("/v8", methods=["GET", "OPTIONS"])
async def v8_frontend_base_route(request: Request):
    """ResonantGenesis V8 frontend base - OWNER ONLY access."""
    from starlette.responses import JSONResponse, FileResponse
    import os
    
    # Check authentication - must have platform_owner role
    user_role = getattr(request.state, 'role', None)
    if user_role != 'platform_owner':
        return JSONResponse(
            status_code=403,
            content={"error": "Access denied. Platform owner authentication required."}
        )
    
    index_path = "/var/www/resonantgenesis_v8/index.html"
    if os.path.isfile(index_path):
        return FileResponse(index_path)
    
    return JSONResponse(status_code=404, content={"error": "V8 frontend not found"})


# ============================================
# CATCH-ALL BILLING ROUTES (MUST BE LAST)
# ============================================
# ORG-level billing routes → agent_engine_service (overview, usage, plans, checkout)
# NOTE: This catch-all MUST be LAST after all specific /billing/* routes
@router.api_route("/billing/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def billing_route(path: str, request: Request):
    """Billing Service API routes - ORG level → agent_engine_service."""
    return await proxy("billing", f"billing/{path}", request)
