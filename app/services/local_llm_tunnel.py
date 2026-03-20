"""
Local LLM Tunnel Service — WebSocket relay for per-user local LLM connections.

Architecture:
  1. User's browser opens a WebSocket to /ws/local-llm/tunnel (authenticated)
  2. Browser acts as a bridge to the user's local Ollama/LM Studio
  3. When chat_service needs local LLM for this user, gateway sends the request
     through the WebSocket → browser makes local HTTP call → returns response

This allows any user to connect their own local LLM without SSH tunnels.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Dict, Optional, Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class TunnelConnection:
    """Represents one user's active local LLM tunnel."""

    def __init__(self, user_id: str, ws: WebSocket, endpoint_url: str):
        self.user_id = user_id
        self.ws = ws
        self.endpoint_url = endpoint_url
        self.connected_at = time.time()
        self.last_heartbeat = time.time()
        self.pending_requests: Dict[str, asyncio.Future] = {}
        self.models: list = []

    async def send_request(self, request_id: str, payload: dict, timeout: float = 120.0) -> dict:
        """Send a completion request through the tunnel and wait for response."""
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_requests[request_id] = future

        try:
            await self.ws.send_json({
                "type": "llm_request",
                "request_id": request_id,
                "payload": payload,
            })
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            logger.warning(f"Tunnel request {request_id} timed out for user {self.user_id}")
            raise Exception("Local LLM request timed out — your model may be too slow or disconnected")
        finally:
            self.pending_requests.pop(request_id, None)

    def resolve_request(self, request_id: str, response: dict):
        """Resolve a pending request with the response from the browser."""
        future = self.pending_requests.get(request_id)
        if future and not future.done():
            future.set_result(response)


class LocalLLMTunnelManager:
    """Manages per-user WebSocket tunnels for local LLM connections."""

    def __init__(self):
        self._tunnels: Dict[str, TunnelConnection] = {}

    @property
    def active_count(self) -> int:
        return len(self._tunnels)

    def get_tunnel(self, user_id: str) -> Optional[TunnelConnection]:
        return self._tunnels.get(user_id)

    def has_tunnel(self, user_id: str) -> bool:
        return user_id in self._tunnels

    async def register(self, user_id: str, ws: WebSocket, endpoint_url: str) -> TunnelConnection:
        """Register a new tunnel connection for a user."""
        # Close any existing tunnel for this user
        old = self._tunnels.get(user_id)
        if old:
            try:
                await old.ws.close(code=1000, reason="Replaced by new connection")
            except Exception:
                pass

        conn = TunnelConnection(user_id, ws, endpoint_url)
        self._tunnels[user_id] = conn
        logger.info(f"Local LLM tunnel registered for user {user_id} → {endpoint_url}")
        return conn

    def unregister(self, user_id: str):
        """Remove a tunnel connection."""
        if user_id in self._tunnels:
            del self._tunnels[user_id]
            logger.info(f"Local LLM tunnel disconnected for user {user_id}")

    async def proxy_completion(
        self,
        user_id: str,
        messages: list,
        model: str = "llama3.1:8b",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> dict:
        """Proxy a chat completion request through the user's tunnel."""
        tunnel = self._tunnels.get(user_id)
        if not tunnel:
            raise Exception("No local LLM tunnel active — open ResonantGenesis in your browser to connect")

        request_id = str(uuid.uuid4())
        payload = {
            "endpoint_url": tunnel.endpoint_url,
            "method": "chat",
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        return await tunnel.send_request(request_id, payload)

    def status(self, user_id: str) -> dict:
        """Get tunnel status for a user."""
        tunnel = self._tunnels.get(user_id)
        if not tunnel:
            return {"connected": False}
        return {
            "connected": True,
            "endpoint_url": tunnel.endpoint_url,
            "connected_at": tunnel.connected_at,
            "last_heartbeat": tunnel.last_heartbeat,
            "models": tunnel.models,
            "uptime_seconds": int(time.time() - tunnel.connected_at),
        }

    def all_status(self) -> dict:
        """Admin view of all tunnels."""
        return {
            "active_tunnels": self.active_count,
            "tunnels": {
                uid: {
                    "endpoint_url": t.endpoint_url,
                    "connected_at": t.connected_at,
                    "models": t.models,
                }
                for uid, t in self._tunnels.items()
            },
        }


# Singleton
tunnel_manager = LocalLLMTunnelManager()
