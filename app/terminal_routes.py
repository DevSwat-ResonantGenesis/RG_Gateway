"""Terminal API Routes.

Proxies terminal requests to ide_platform_service which runs real PTY sessions.
Also handles legacy /terminal/session/* endpoints for backward compatibility.
"""

import os
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/terminal", tags=["terminal"])

IDE_SERVICE_URL = os.getenv("IDE_SERVICE_URL", "http://ide_platform_service:8080")


# ============================================
# Proxy helper
# ============================================

async def proxy_to_ide(method: str, path: str, request: Request = None, json_body: dict = None):
    """Proxy a request to the ide_platform_service."""
    url = f"{IDE_SERVICE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if method == "GET":
                params = dict(request.query_params) if request else {}
                resp = await client.get(url, params=params)
            elif method == "POST":
                body = json_body
                if body is None and request:
                    body = await request.json()
                resp = await client.post(url, json=body)
            elif method == "DELETE":
                resp = await client.delete(url)
            else:
                resp = await client.request(method, url)
            
            return resp.json()
    except httpx.ConnectError:
        logger.warning(f"IDE service unavailable at {IDE_SERVICE_URL}")
        raise HTTPException(status_code=503, detail="IDE service unavailable")
    except Exception as e:
        logger.error(f"Terminal proxy error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# Request Models
# ============================================

class TerminalCreateRequest(BaseModel):
    project_id: Optional[str] = None
    shell: Optional[str] = None
    cwd: Optional[str] = None

class TerminalExecuteRequest(BaseModel):
    command: str
    timeout: int = 30


# ============================================
# Terminal Session Endpoints (proxy to ide_platform_service)
# ============================================

@router.post("/session/create")
async def create_terminal_session(payload: TerminalCreateRequest, request: Request):
    """Create a new terminal session - proxied to ide_platform_service."""
    return await proxy_to_ide("POST", "/terminal/sessions", json_body={
        "project_id": payload.project_id,
        "shell": payload.shell,
        "cwd": payload.cwd,
    })


@router.get("/sessions")
async def list_terminal_sessions(request: Request):
    """List all terminal sessions - proxied to ide_platform_service."""
    return await proxy_to_ide("GET", "/terminal/sessions", request)


@router.post("/session/{session_id}/execute")
async def execute_in_terminal(session_id: str, payload: TerminalExecuteRequest, request: Request):
    """Execute a command in a terminal session - proxied to ide_platform_service."""
    return await proxy_to_ide("POST", f"/terminal/sessions/{session_id}/execute", json_body={
        "command": payload.command,
        "timeout": payload.timeout,
    })


@router.post("/session/{session_id}/input")
async def send_terminal_input(session_id: str, payload: TerminalExecuteRequest, request: Request):
    """Send raw input to terminal - proxied to ide_platform_service."""
    return await proxy_to_ide("POST", f"/terminal/sessions/{session_id}/input", json_body={
        "command": payload.command,
    })


@router.get("/session/{session_id}/output")
async def get_terminal_output(session_id: str, request: Request):
    """Get terminal output - proxied to ide_platform_service."""
    return await proxy_to_ide("GET", f"/terminal/sessions/{session_id}/output", request)


@router.delete("/session/{session_id}")
async def delete_terminal_session(session_id: str, request: Request):
    """Delete a terminal session - proxied to ide_platform_service."""
    return await proxy_to_ide("DELETE", f"/terminal/sessions/{session_id}")


@router.post("/execute")
async def execute_command_simple(payload: TerminalExecuteRequest, request: Request):
    """Execute a single command without a session - proxied to ide_platform_service."""
    return await proxy_to_ide("POST", "/terminal/execute", json_body={
        "command": payload.command,
        "timeout": payload.timeout,
    })
