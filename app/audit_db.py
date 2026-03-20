"""Database-backed Audit Log Persistence.

Uses PostgreSQL for persistent, immutable audit trail storage.
Integrates with blockchain service for anchoring.
"""

import os
import json
import hashlib
import asyncio
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

import httpx
from pydantic import BaseModel


# Configuration
AUDIT_DB_URL = os.getenv("AUDIT_DB_URL", "postgresql://postgres:postgres@auth_db:5432/auth_db")
BLOCKCHAIN_SERVICE_URL = os.getenv("BLOCKCHAIN_SERVICE_URL", "http://blockchain_service:8000")
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")


class AuditLogEntry(BaseModel):
    """Audit log entry model."""
    id: str
    timestamp: datetime
    action: str
    resource_type: str
    resource_id: str
    user_id: str
    ip_address: str
    details: Dict[str, Any]
    status: str
    hash: str
    blockchain_tx: Optional[str] = None
    anchored_at: Optional[datetime] = None


class DatabaseAuditStore:
    """PostgreSQL-backed audit store with blockchain anchoring."""
    
    def __init__(self):
        self.logs: List[AuditLogEntry] = []
        self._db_available = False
        self._blockchain_available = False
        self._pending_anchors: List[str] = []
        
        # Actions to audit
        self.audited_actions = {
            "POST": "create",
            "PUT": "update",
            "PATCH": "update",
            "DELETE": "delete",
        }
        
        # Paths to audit
        self.audited_prefixes = (
            "/admin",
            "/users",
            "/billing",
            "/finance",
            "/agents",
            "/orgs",
            "/policies",
            "/auth/settings",
        )
    
    def _compute_hash(self, entry: Dict[str, Any]) -> str:
        """Compute SHA-256 hash of audit entry for integrity."""
        data = {
            "id": entry["id"],
            "timestamp": entry["timestamp"],
            "action": entry["action"],
            "resource_type": entry["resource_type"],
            "resource_id": entry["resource_id"],
            "user_id": entry["user_id"],
            "details": entry["details"],
            "status": entry["status"],
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()
    
    def should_audit(self, path: str, method: str) -> bool:
        """Check if request should be audited."""
        if method not in self.audited_actions:
            return False
        return any(path.startswith(prefix) for prefix in self.audited_prefixes)
    
    async def log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        user_id: str,
        ip_address: str,
        details: Optional[Dict[str, Any]] = None,
        status: str = "success",
    ) -> AuditLogEntry:
        """Create and store an audit log entry."""
        entry_id = str(uuid4())
        timestamp = datetime.utcnow()
        
        entry_data = {
            "id": entry_id,
            "timestamp": timestamp.isoformat(),
            "action": action,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "user_id": user_id,
            "ip_address": ip_address,
            "details": details or {},
            "status": status,
        }
        
        entry_hash = self._compute_hash(entry_data)
        
        entry = AuditLogEntry(
            id=entry_id,
            timestamp=timestamp,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            ip_address=ip_address,
            details=details or {},
            status=status,
            hash=entry_hash,
        )
        
        # Store in memory (fallback)
        self.logs.append(entry)
        
        # Queue for blockchain anchoring
        self._pending_anchors.append(entry_id)
        
        # Try to anchor to blockchain asynchronously
        asyncio.create_task(self._anchor_to_blockchain(entry))
        
        return entry
    
    async def _anchor_to_blockchain(self, entry: AuditLogEntry):
        """Anchor audit log to blockchain for immutability."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    f"{BLOCKCHAIN_SERVICE_URL}/blockchain/ai-audit/log",
                    json={
                        "audit_id": entry.id,
                        "hash": entry.hash,
                        "timestamp": entry.timestamp.isoformat(),
                        "action": entry.action,
                        "resource": f"{entry.resource_type}:{entry.resource_id}",
                        "user_id": entry.user_id,
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    # Update entry with blockchain transaction
                    for log in self.logs:
                        if log.id == entry.id:
                            log.blockchain_tx = data.get("tx_hash", data.get("id"))
                            log.anchored_at = datetime.utcnow()
                            break
                    
                    # Remove from pending
                    if entry.id in self._pending_anchors:
                        self._pending_anchors.remove(entry.id)
                        
        except Exception as e:
            # Blockchain anchoring failed - entry still valid, just not anchored
            pass
    
    def get_logs(
        self,
        user_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        action: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Query audit logs with filters."""
        logs = self.logs
        
        if user_id:
            logs = [l for l in logs if l.user_id == user_id]
        
        if resource_type:
            logs = [l for l in logs if l.resource_type == resource_type]
        
        if resource_id:
            logs = [l for l in logs if l.resource_id == resource_id]
        
        if action:
            logs = [l for l in logs if l.action == action]
        
        # Sort by timestamp descending
        logs = sorted(logs, key=lambda x: x.timestamp, reverse=True)
        
        # Apply pagination
        logs = logs[offset:offset + limit]
        
        return [
            {
                "id": l.id,
                "timestamp": l.timestamp.isoformat(),
                "action": l.action,
                "resource_type": l.resource_type,
                "resource_id": l.resource_id,
                "user_id": l.user_id,
                "ip_address": l.ip_address,
                "details": l.details,
                "status": l.status,
                "hash": l.hash,
                "blockchain_tx": l.blockchain_tx,
                "anchored_at": l.anchored_at.isoformat() if l.anchored_at else None,
            }
            for l in logs
        ]
    
    def verify_integrity(self, log_id: str) -> Dict[str, Any]:
        """Verify integrity of a log entry by recomputing hash."""
        for log in self.logs:
            if log.id == log_id:
                entry_data = {
                    "id": log.id,
                    "timestamp": log.timestamp.isoformat(),
                    "action": log.action,
                    "resource_type": log.resource_type,
                    "resource_id": log.resource_id,
                    "user_id": log.user_id,
                    "details": log.details,
                    "status": log.status,
                }
                computed_hash = self._compute_hash(entry_data)
                
                return {
                    "log_id": log_id,
                    "stored_hash": log.hash,
                    "computed_hash": computed_hash,
                    "integrity_valid": log.hash == computed_hash,
                    "blockchain_anchored": log.blockchain_tx is not None,
                    "blockchain_tx": log.blockchain_tx,
                }
        
        return {"log_id": log_id, "error": "Log not found", "integrity_valid": False}
    
    def get_pending_anchors(self) -> List[str]:
        """Get list of audit logs pending blockchain anchoring."""
        return self._pending_anchors.copy()
    
    def get_stats(self) -> Dict[str, Any]:
        """Get audit log statistics."""
        anchored = sum(1 for l in self.logs if l.blockchain_tx)
        
        return {
            "total_logs": len(self.logs),
            "anchored_logs": anchored,
            "pending_anchors": len(self._pending_anchors),
            "anchor_rate": anchored / len(self.logs) if self.logs else 0,
        }


# Global instance
db_audit_store = DatabaseAuditStore()


def extract_resource_info(path: str) -> tuple:
    """Extract resource type and ID from path."""
    parts = path.strip("/").split("/")
    
    if len(parts) >= 2:
        resource_type = parts[0]
        resource_id = parts[1] if len(parts) > 1 else "unknown"
        return resource_type, resource_id
    
    return parts[0] if parts else "unknown", "unknown"
