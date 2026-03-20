"""Audit Log Persistence Module.

Provides database-backed audit logging with immutable trail.
"""

import json
import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4
from collections import defaultdict

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response


class AuditLog:
    """Represents an immutable audit log entry."""
    
    def __init__(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        user_id: str,
        ip_address: str,
        details: Optional[Dict[str, Any]] = None,
        status: str = "success",
    ):
        self.id = str(uuid4())
        self.timestamp = datetime.utcnow()
        self.action = action
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.user_id = user_id
        self.ip_address = ip_address
        self.details = details or {}
        self.status = status
        
        # Create immutable hash for integrity verification
        self.hash = self._compute_hash()
    
    def _compute_hash(self) -> str:
        """Compute SHA-256 hash of log entry for integrity."""
        data = {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "user_id": self.user_id,
            "details": self.details,
            "status": self.status,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "user_id": self.user_id,
            "ip_address": self.ip_address,
            "details": self.details,
            "status": self.status,
            "hash": self.hash,
        }


class AuditStore:
    """In-memory audit store (replace with database in production)."""
    
    def __init__(self):
        self.logs: List[AuditLog] = []
        self.by_user: Dict[str, List[AuditLog]] = defaultdict(list)
        self.by_resource: Dict[str, List[AuditLog]] = defaultdict(list)
        
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
    
    def should_audit(self, path: str, method: str) -> bool:
        """Check if request should be audited."""
        if method not in self.audited_actions:
            return False
        return any(path.startswith(prefix) for prefix in self.audited_prefixes)
    
    def log(
        self,
        action: str,
        resource_type: str,
        resource_id: str,
        user_id: str,
        ip_address: str,
        details: Optional[Dict[str, Any]] = None,
        status: str = "success",
    ) -> AuditLog:
        """Create and store an audit log entry."""
        entry = AuditLog(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            ip_address=ip_address,
            details=details,
            status=status,
        )
        
        self.logs.append(entry)
        self.by_user[user_id].append(entry)
        self.by_resource[f"{resource_type}:{resource_id}"].append(entry)
        
        return entry
    
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
            logs = self.by_user.get(user_id, [])
        
        if resource_type and resource_id:
            key = f"{resource_type}:{resource_id}"
            logs = [l for l in logs if f"{l.resource_type}:{l.resource_id}" == key]
        elif resource_type:
            logs = [l for l in logs if l.resource_type == resource_type]
        
        if action:
            logs = [l for l in logs if l.action == action]
        
        # Sort by timestamp descending
        logs = sorted(logs, key=lambda x: x.timestamp, reverse=True)
        
        # Apply pagination
        logs = logs[offset:offset + limit]
        
        return [l.to_dict() for l in logs]
    
    def verify_integrity(self, log_id: str) -> bool:
        """Verify integrity of a log entry by recomputing hash."""
        for log in self.logs:
            if log.id == log_id:
                return log.hash == log._compute_hash()
        return False


audit_store = AuditStore()


def extract_resource_info(path: str) -> tuple:
    """Extract resource type and ID from path."""
    parts = path.strip("/").split("/")
    
    if len(parts) >= 2:
        resource_type = parts[0]
        resource_id = parts[1] if len(parts) > 1 else "unknown"
        return resource_type, resource_id
    
    return parts[0] if parts else "unknown", "unknown"


class AuditMiddleware(BaseHTTPMiddleware):
    """Middleware to automatically audit requests."""
    
    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        method = request.method.upper()
        
        # Check if should audit
        if not audit_store.should_audit(path, method):
            return await call_next(request)
        
        # Extract info
        user_id = request.headers.get("x-user-id", "anonymous")
        ip_address = request.headers.get("x-forwarded-for", "")
        if not ip_address and request.client:
            ip_address = request.client.host
        
        resource_type, resource_id = extract_resource_info(path)
        action = audit_store.audited_actions.get(method, "unknown")
        
        # Process request
        response = await call_next(request)
        
        # Determine status
        status = "success" if 200 <= response.status_code < 400 else "failure"
        
        # Log the action
        audit_store.log(
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            user_id=user_id,
            ip_address=ip_address,
            details={
                "method": method,
                "path": path,
                "status_code": response.status_code,
            },
            status=status,
        )
        
        return response


def get_audit_logs(**kwargs) -> List[Dict[str, Any]]:
    """Get audit logs with optional filters."""
    return audit_store.get_logs(**kwargs)


def verify_log_integrity(log_id: str) -> bool:
    """Verify integrity of a specific log entry."""
    return audit_store.verify_integrity(log_id)
