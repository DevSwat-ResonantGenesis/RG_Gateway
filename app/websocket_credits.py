"""
Real-time Credit Updates via WebSocket - Phase 2.1 GTM

Provides real-time credit balance updates to connected clients.
Users can subscribe to their credit balance and receive instant
updates when credits are deducted or added.
"""

import logging
import json
import asyncio
from typing import Dict, Set, Optional, Any
from datetime import datetime
from dataclasses import dataclass, asdict

from fastapi import WebSocket, WebSocketDisconnect, HTTPException
from starlette.websockets import WebSocketState

logger = logging.getLogger(__name__)


@dataclass
class CreditUpdate:
    """Credit update message."""
    type: str  # "credit_update", "alert", "balance"
    user_id: str
    balance: int
    change: Optional[int] = None
    reason: Optional[str] = None
    alert_level: Optional[str] = None  # "warning_80", "critical_90", "exhausted_100"
    timestamp: str = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}
    
    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class CreditWebSocketManager:
    """
    Manage WebSocket connections for real-time credit updates.
    
    Features:
    - Multiple connections per user (different devices/tabs)
    - Automatic cleanup on disconnect
    - Broadcast to all user connections
    - Heartbeat/ping-pong for connection health
    """
    
    def __init__(self):
        # user_id -> set of WebSocket connections
        self._connections: Dict[str, Set[WebSocket]] = {}
        # WebSocket -> user_id (reverse lookup)
        self._user_lookup: Dict[WebSocket, str] = {}
        # Connection count for metrics
        self._total_connections = 0
    
    @property
    def total_connections(self) -> int:
        return self._total_connections
    
    @property
    def unique_users(self) -> int:
        return len(self._connections)
    
    async def connect(self, websocket: WebSocket, user_id: str) -> bool:
        """
        Accept and register a WebSocket connection.
        
        Args:
            websocket: WebSocket connection
            user_id: User ID to associate with connection
            
        Returns:
            True if connected successfully
        """
        try:
            await websocket.accept()
            
            if user_id not in self._connections:
                self._connections[user_id] = set()
            
            self._connections[user_id].add(websocket)
            self._user_lookup[websocket] = user_id
            self._total_connections += 1
            
            logger.info(
                f"WebSocket connected: user={user_id[:8]}... "
                f"(total: {self._total_connections}, users: {self.unique_users})"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            return False
    
    def disconnect(self, websocket: WebSocket) -> Optional[str]:
        """
        Remove a WebSocket connection.
        
        Args:
            websocket: WebSocket to disconnect
            
        Returns:
            User ID that was disconnected, or None
        """
        user_id = self._user_lookup.pop(websocket, None)
        
        if user_id and user_id in self._connections:
            self._connections[user_id].discard(websocket)
            self._total_connections -= 1
            
            # Clean up empty user sets
            if not self._connections[user_id]:
                del self._connections[user_id]
            
            logger.info(
                f"WebSocket disconnected: user={user_id[:8]}... "
                f"(total: {self._total_connections})"
            )
        
        return user_id
    
    async def send_to_user(self, user_id: str, message: CreditUpdate) -> int:
        """
        Send message to all connections for a user.
        
        Args:
            user_id: Target user ID
            message: CreditUpdate message
            
        Returns:
            Number of connections message was sent to
        """
        if user_id not in self._connections:
            return 0
        
        sent_count = 0
        dead_connections = set()
        
        for ws in self._connections[user_id].copy():
            try:
                if ws.client_state == WebSocketState.CONNECTED:
                    await ws.send_text(message.to_json())
                    sent_count += 1
                else:
                    dead_connections.add(ws)
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                dead_connections.add(ws)
        
        # Clean up dead connections
        for ws in dead_connections:
            self.disconnect(ws)
        
        return sent_count
    
    async def broadcast_credit_update(
        self,
        user_id: str,
        balance: int,
        change: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> int:
        """
        Broadcast credit update to user.
        
        Args:
            user_id: User ID
            balance: New balance
            change: Amount changed (negative for deduction)
            reason: Reason for change
            
        Returns:
            Number of connections notified
        """
        update = CreditUpdate(
            type="credit_update",
            user_id=user_id,
            balance=balance,
            change=change,
            reason=reason,
        )
        
        return await self.send_to_user(user_id, update)
    
    async def broadcast_alert(
        self,
        user_id: str,
        balance: int,
        alert_level: str,
    ) -> int:
        """
        Broadcast usage alert to user.
        
        Args:
            user_id: User ID
            balance: Current balance
            alert_level: Alert level (warning_80, critical_90, exhausted_100)
            
        Returns:
            Number of connections notified
        """
        update = CreditUpdate(
            type="alert",
            user_id=user_id,
            balance=balance,
            alert_level=alert_level,
        )
        
        return await self.send_to_user(user_id, update)
    
    async def send_balance(self, user_id: str, balance: int) -> int:
        """
        Send current balance to user (e.g., on connect).
        
        Args:
            user_id: User ID
            balance: Current balance
            
        Returns:
            Number of connections notified
        """
        update = CreditUpdate(
            type="balance",
            user_id=user_id,
            balance=balance,
        )
        
        return await self.send_to_user(user_id, update)
    
    def get_user_connection_count(self, user_id: str) -> int:
        """Get number of connections for a user."""
        return len(self._connections.get(user_id, set()))
    
    def is_user_connected(self, user_id: str) -> bool:
        """Check if user has any active connections."""
        return user_id in self._connections and len(self._connections[user_id]) > 0


# Global manager instance
credit_ws_manager = CreditWebSocketManager()


async def handle_credit_websocket(websocket: WebSocket, user_id: str):
    """
    Handle a credit WebSocket connection.
    
    Protocol:
    - Client sends "ping" -> Server responds "pong"
    - Client sends "balance" -> Server sends current balance
    - Server pushes credit updates automatically
    
    Args:
        websocket: WebSocket connection
        user_id: Authenticated user ID
    """
    connected = await credit_ws_manager.connect(websocket, user_id)
    if not connected:
        return
    
    try:
        # Send initial balance
        # In production, fetch from billing service
        # await credit_ws_manager.send_balance(user_id, current_balance)
        
        while True:
            try:
                # Wait for client messages with timeout
                data = await asyncio.wait_for(
                    websocket.receive_text(),
                    timeout=60.0  # 60 second timeout
                )
                
                # Handle client messages
                if data == "ping":
                    await websocket.send_text("pong")
                elif data == "balance":
                    # Client requesting balance refresh
                    # In production, fetch from billing service
                    pass
                else:
                    # Unknown message, ignore
                    logger.debug(f"Unknown WebSocket message: {data}")
                    
            except asyncio.TimeoutError:
                # Send ping to check if connection is alive
                try:
                    await websocket.send_text('{"type":"ping"}')
                except:
                    break
                    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        credit_ws_manager.disconnect(websocket)


# ============================================
# INTEGRATION HELPERS
# ============================================

async def notify_credit_change(
    user_id: str,
    new_balance: int,
    change: int,
    reason: str,
):
    """
    Notify user of credit change via WebSocket.
    
    Call this after any credit deduction or addition.
    
    Args:
        user_id: User ID
        new_balance: Balance after change
        change: Amount changed (negative for deduction)
        reason: Reason for change
    """
    if credit_ws_manager.is_user_connected(user_id):
        await credit_ws_manager.broadcast_credit_update(
            user_id=user_id,
            balance=new_balance,
            change=change,
            reason=reason,
        )


async def notify_usage_alert(
    user_id: str,
    balance: int,
    tier_credits: int,
):
    """
    Check and notify user of usage alerts.
    
    Args:
        user_id: User ID
        balance: Current balance
        tier_credits: Total credits for tier
    """
    if tier_credits <= 0:  # Unlimited
        return
    
    usage_percent = ((tier_credits - balance) / tier_credits) * 100
    
    alert_level = None
    if usage_percent >= 100:
        alert_level = "exhausted_100"
    elif usage_percent >= 90:
        alert_level = "critical_90"
    elif usage_percent >= 80:
        alert_level = "warning_80"
    
    if alert_level and credit_ws_manager.is_user_connected(user_id):
        await credit_ws_manager.broadcast_alert(
            user_id=user_id,
            balance=balance,
            alert_level=alert_level,
        )
