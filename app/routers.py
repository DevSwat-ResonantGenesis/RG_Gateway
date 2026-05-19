from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
from starlette.responses import JSONResponse
import httpx
import websockets
import asyncio
import json

from .reverse_proxy import proxy, proxy_public

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


# Voice session REMOVED — voice_ws.py deleted


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


# IDE WebSocket proxy REMOVED — ide_platform_service killed


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


# Rabbit routes REMOVED — rabbit_api killed
# Cognitive routes REMOVED — cognitive_service killed


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

@router.api_route("/webhooks/agent/{agent_id}/events", methods=["GET", "OPTIONS"])
async def webhook_events_route(agent_id: str, request: Request):
    """List webhook event audit log for an agent."""
    return await proxy("agents", f"webhooks/agent/{agent_id}/events", request)


# ============================================
# FEDERATION — External agents on user hardware
# ============================================

@router.api_route("/federation/register", methods=["POST", "OPTIONS"])
async def federation_register_route(request: Request):
    """Register an external agent running on user hardware."""
    return await proxy("agents", "federation/register", request)

@router.api_route("/federation/heartbeat", methods=["POST", "OPTIONS"])
async def federation_heartbeat_route(request: Request):
    """Heartbeat from a federated agent."""
    return await proxy("agents", "federation/heartbeat", request)

@router.api_route("/federation/agents", methods=["GET", "OPTIONS"])
async def federation_list_agents_route(request: Request):
    """List user's federated agents with connection status."""
    return await proxy("agents", "federation/agents", request)

@router.api_route("/federation/disconnect/{agent_id}", methods=["POST", "OPTIONS"])
async def federation_disconnect_route(agent_id: str, request: Request):
    """Disconnect a federated agent."""
    return await proxy("agents", f"federation/disconnect/{agent_id}", request)


# ============================================
# OPENCLAW FEDERATION SERVICE ROUTES
# ============================================
# All OpenClaw traffic routes through the gateway's existing HTTPS endpoint.
# openclaw_service is internal-only (zero ports exposed to the internet).
# Auth: Gateway JWT middleware handles authentication for all non-public routes.
# Public: relay (HMAC-verified by openclaw_service) and health only.

# --- Public (no auth) ---

@router.api_route("/openclaw/health", methods=["GET", "OPTIONS"])
async def openclaw_health_route(request: Request):
    """OpenClaw service health — public, no auth."""
    return await proxy_public("openclaw", "health", request)

@router.api_route("/openclaw/manifest", methods=["GET", "OPTIONS"])
async def openclaw_manifest_route(request: Request):
    """ClawHub skill manifest — public discovery."""
    return await proxy_public("openclaw", "manifest", request)

@router.api_route("/openclaw/setup-guide", methods=["GET", "OPTIONS"])
async def openclaw_setup_guide_route(request: Request):
    """Setup instructions — public."""
    return await proxy_public("openclaw", "setup-guide", request)

@router.api_route("/openclaw/relay/{agent_id}", methods=["POST", "OPTIONS"])
async def openclaw_relay_route(agent_id: str, request: Request):
    """Public webhook relay — HMAC signature verified by openclaw_service."""
    return await proxy_public("openclaw", f"relay/{agent_id}", request)

# --- Authenticated (JWT required via gateway middleware) ---

@router.api_route("/openclaw/status", methods=["GET", "OPTIONS"])
async def openclaw_status_route(request: Request):
    """Connection status for authenticated user."""
    return await proxy("openclaw", "status", request)

@router.api_route("/openclaw/connections", methods=["GET", "POST", "OPTIONS"])
async def openclaw_connections_route(request: Request):
    """List or create OpenClaw connections (authenticated)."""
    return await proxy("openclaw", "connections", request)

@router.api_route("/openclaw/connections/{trigger_id}/pause", methods=["POST", "OPTIONS"])
async def openclaw_connection_pause_route(trigger_id: str, request: Request):
    """Pause an OpenClaw connection."""
    return await proxy("openclaw", f"connections/{trigger_id}/pause", request)

@router.api_route("/openclaw/connections/{trigger_id}/resume", methods=["POST", "OPTIONS"])
async def openclaw_connection_resume_route(trigger_id: str, request: Request):
    """Resume an OpenClaw connection."""
    return await proxy("openclaw", f"connections/{trigger_id}/resume", request)

@router.api_route("/openclaw/connections/{trigger_id}", methods=["DELETE", "OPTIONS"])
async def openclaw_connection_delete_route(trigger_id: str, request: Request):
    """Delete an OpenClaw connection."""
    return await proxy("openclaw", f"connections/{trigger_id}", request)

# --- Agent Registration & Lifecycle ---

@router.api_route("/openclaw/agents/register", methods=["POST", "OPTIONS"])
async def openclaw_agent_register_route(request: Request):
    """Register an OpenClaw agent on the platform (authenticated)."""
    return await proxy("openclaw", "agents/register", request)

@router.api_route("/openclaw/agents/openclaw", methods=["GET", "OPTIONS"])
async def openclaw_agents_list_route(request: Request):
    """List user's OpenClaw agents."""
    return await proxy("openclaw", "agents/openclaw", request)

@router.api_route("/openclaw/agents/heartbeat", methods=["POST", "OPTIONS"])
async def openclaw_agent_heartbeat_route(request: Request):
    """Heartbeat from OpenClaw agent on user hardware."""
    return await proxy("openclaw", "agents/heartbeat", request)

# --- Skills Federation ---

@router.api_route("/openclaw/skills/available", methods=["GET", "OPTIONS"])
async def openclaw_skills_available_route(request: Request):
    """List all platform tools available to OpenClaw agents."""
    return await proxy("openclaw", "skills/available", request)

@router.api_route("/openclaw/skills/execute", methods=["POST", "OPTIONS"])
async def openclaw_skills_execute_route(request: Request):
    """Execute a platform skill on behalf of an OpenClaw agent."""
    return await proxy("openclaw", "skills/execute", request)

@router.api_route("/openclaw/skills/import", methods=["POST", "OPTIONS"])
async def openclaw_skills_import_route(request: Request):
    """Import a custom skill from an OpenClaw agent."""
    return await proxy("openclaw", "skills/import", request)

# --- Memory Bridge ---

@router.api_route("/openclaw/memory/ingest", methods=["POST", "OPTIONS"])
async def openclaw_memory_ingest_route(request: Request):
    """Ingest memory from OpenClaw agent into Hash Sphere."""
    return await proxy("openclaw", "memory/ingest", request)

@router.api_route("/openclaw/memory/query", methods=["POST", "OPTIONS"])
async def openclaw_memory_query_route(request: Request):
    """Query Hash Sphere memories for an OpenClaw agent."""
    return await proxy("openclaw", "memory/query", request)

# --- Governance & Marketplace ---

@router.api_route("/openclaw/governance/enroll", methods=["POST", "OPTIONS"])
async def openclaw_governance_enroll_route(request: Request):
    """Enroll OpenClaw agent in RARA governance."""
    return await proxy("openclaw", "governance/enroll", request)

@router.api_route("/openclaw/governance/{agent_id}", methods=["GET", "OPTIONS"])
async def openclaw_governance_status_route(agent_id: str, request: Request):
    """Governance status for an OpenClaw agent."""
    return await proxy("openclaw", f"governance/{agent_id}", request)

@router.api_route("/openclaw/marketplace/list", methods=["POST", "OPTIONS"])
async def openclaw_marketplace_list_route(request: Request):
    """List an OpenClaw agent on the marketplace."""
    return await proxy("openclaw", "marketplace/list", request)

# --- LLM Proxy (OpenAI-compatible) ---

@router.api_route("/openclaw/v1/chat/completions", methods=["POST", "OPTIONS"])
async def openclaw_llm_proxy_route(request: Request):
    """OpenAI-compatible LLM proxy — routes through platform Unified LLM Service."""
    return await proxy("openclaw", "v1/chat/completions", request)


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


# ============================================
# MARKETPLACE ROUTES
# ============================================

@router.api_route("/marketplace/health", methods=["GET", "OPTIONS"])
async def marketplace_health(request: Request):
    """Marketplace Service health check."""
    return await proxy("marketplace", "health", request)

@router.api_route("/marketplace/marketplace/listings", methods=["GET", "OPTIONS"])
async def marketplace_listings_public(request: Request):
    """Public marketplace listings (no auth required)."""
    return await proxy_public("marketplace", "marketplace/listings", request)

@router.api_route("/marketplace/marketplace/categories", methods=["GET", "OPTIONS"])
async def marketplace_categories_public(request: Request):
    """Public marketplace categories."""
    return await proxy_public("marketplace", "marketplace/categories", request)

@router.api_route("/marketplace/marketplace/stats", methods=["GET", "OPTIONS"])
async def marketplace_stats_public(request: Request):
    """Public marketplace stats."""
    return await proxy_public("marketplace", "marketplace/stats", request)

@router.api_route("/marketplace/marketplace/featured", methods=["GET", "OPTIONS"])
async def marketplace_featured_public(request: Request):
    """Public featured agents."""
    return await proxy_public("marketplace", "marketplace/featured", request)

@router.api_route("/marketplace/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def marketplace_route(path: str, request: Request):
    """Marketplace Service API routes."""
    return await proxy("marketplace", f"marketplace/{path}", request)


# ============================================
# TERMINAL ROUTES - handled by terminal_routes.py (included in main.py)
# ============================================
# Note: /terminal/session/* endpoints are now served directly by the gateway via terminal_routes.py


# IDE debugger/terminal routes REMOVED — ide_platform_service killed


# ============================================
# PREDICTIONS ROUTES - handled by predictions_routes.py (included in main.py)
# ============================================
# Note: /predictions/* endpoints are now served directly by the gateway


# ============================================
# ANCHORS ROUTES - handled by anchors_routes.py (included in main.py)
# ============================================
# Note: /anchors/* endpoints are now served directly by the gateway via anchors_routes.py


# V8 routes REMOVED — v8_api_service killed


# ============================================
# CATCH-ALL BILLING ROUTES (MUST BE LAST)
# ============================================
# ORG-level billing routes → agent_engine_service (overview, usage, plans, checkout)
# NOTE: This catch-all MUST be LAST after all specific /billing/* routes
@router.api_route("/billing/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def billing_route(path: str, request: Request):
    """Billing Service API routes - ORG level → agent_engine_service."""
    return await proxy("billing", f"billing/{path}", request)
