"""Persistent revocation store using Redis for multi-container durability.

Author: Resonant Genesis Team
Updated: January 11, 2026
"""

import json
import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, asdict
from enum import Enum
from .redis_failure_handler import redis_failure_handler

class RevocationScope(Enum):
    """Scope of token revocation."""
    USER = "user"
    ORG = "organization"
    ROLE = "role"
    GLOBAL = "global"

@dataclass
class RevocationEntry:
    """Token revocation entry."""
    scope: RevocationScope
    target_id: str
    reason: str
    timestamp: float
    revoked_by: str
    ttl_seconds: int = 3600  # 1 hour default
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for Redis storage."""
        return {
            "scope": self.scope.value,
            "target_id": self.target_id,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "revoked_by": self.revoked_by,
            "ttl_seconds": self.ttl_seconds
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RevocationEntry":
        """Create from dictionary from Redis."""
        return cls(
            scope=RevocationScope(data["scope"]),
            target_id=data["target_id"],
            reason=data["reason"],
            timestamp=data["timestamp"],
            revoked_by=data["revoked_by"],
            ttl_seconds=data.get("ttl_seconds", 3600)
        )
    
    def is_expired(self) -> bool:
        """Check if revocation entry has expired."""
        return time.time() > (self.timestamp + self.ttl_seconds)

class RevocationStore:
    """Redis-backed revocation store for persistence and synchronization."""
    
    def __init__(self, redis_url: str = "redis://redis:6379"):
        self.redis_url = redis_url
        self.key_prefix = "revocation:"
        self.global_key = f"{self.key_prefix}global"
        self.default_ttl = 3600  # 1 hour
        
    async def connect(self):
        """Initialize Redis connection."""
        await redis_failure_handler.connect(self.redis_url)
    
    async def disconnect(self):
        """Close Redis connection."""
        await redis_failure_handler.disconnect()
    
    def _make_key(self, scope: RevocationScope, target_id: str) -> str:
        """Generate Redis key for revocation entry."""
        if scope == RevocationScope.GLOBAL:
            return self.global_key
        return f"{self.key_prefix}{scope.value}:{target_id}"
    
    async def add_revocation(self, entry: RevocationEntry) -> bool:
        """Add a revocation entry to Redis."""
        key = self._make_key(entry.scope, entry.target_id)
        data = entry.to_dict()
        
        # Store with TTL using failure handler
        async def setex_operation(redis_client, k, ttl, val):
            await redis_client.setex(k, ttl, val)
        
        await redis_failure_handler.execute_with_fallback(
            setex_operation, 
            key, 
            entry.ttl_seconds, 
            json.dumps(data)
        )
        
        # Update global timestamp if this is a global revocation
        if entry.scope == RevocationScope.GLOBAL:
            async def set_global_timestamp(redis_client, k, ttl, val):
                await redis_client.setex(k, ttl, val)
            
            await redis_failure_handler.execute_with_fallback(
                set_global_timestamp,
                f"{self.global_key}:timestamp",
                entry.ttl_seconds,
                str(entry.timestamp)
            )
        
        return True
    
    async def get_revocation(self, scope: RevocationScope, target_id: str) -> Optional[RevocationEntry]:
        """Get a specific revocation entry."""
        key = self._make_key(scope, target_id)
        
        async def get_operation(redis_client, k):
            return await redis_client.get(k)
        
        data = await redis_failure_handler.execute_with_fallback(get_operation, key)
        
        if not data:
            return None
        
        try:
            entry_data = json.loads(data)
            entry = RevocationEntry.from_dict(entry_data)
            
            # Check if expired (extra safety check)
            if entry.is_expired():
                await self.delete_revocation(scope, target_id)
                return None
            
            return entry
        except (json.JSONDecodeError, KeyError, ValueError):
            # Corrupted data, remove it
            await self.delete_revocation(scope, target_id)
            return None
    
    async def delete_revocation(self, scope: RevocationScope, target_id: str) -> bool:
        """Delete a specific revocation entry."""
        key = self._make_key(scope, target_id)
        
        async def delete_operation(redis_client, k):
            result = await redis_client.delete(k)
            return result > 0
        
        return await redis_failure_handler.execute_with_fallback(delete_operation, key)
    
    async def is_token_revoked(self, token_claims: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Check if a token is revoked based on its claims."""
        # Check global revocation first
        global_entry = await self.get_revocation(RevocationScope.GLOBAL, "*")
        if global_entry:
            # Extract token issued time (iat claim)
            iat = token_claims.get("iat")
            if iat and isinstance(iat, (int, float)):
                if float(iat) < global_entry.timestamp:
                    return True, f"Global token revocation: {global_entry.reason}"
        
        # Check user revocation
        user_id = token_claims.get("user_id")
        if user_id:
            user_entry = await self.get_revocation(RevocationScope.USER, user_id)
            if user_entry:
                return True, f"User token revoked: {user_entry.reason}"
        
        # Check organization revocation
        org_id = token_claims.get("org_id")
        if org_id:
            org_entry = await self.get_revocation(RevocationScope.ORG, org_id)
            if org_entry:
                return True, f"Organization token revoked: {org_entry.reason}"
        
        # Check role revocation
        role = token_claims.get("role")
        if role:
            role_entry = await self.get_revocation(RevocationScope.ROLE, role)
            if role_entry:
                return True, f"Role token revoked: {role_entry.reason}"
        
        return False, None
    
    async def revoke_user_tokens(self, user_id: str, reason: str, revoked_by: str, ttl_seconds: int = None):
        """Revoke all tokens for a specific user."""
        entry = RevocationEntry(
            scope=RevocationScope.USER,
            target_id=user_id,
            reason=reason,
            timestamp=time.time(),
            revoked_by=revoked_by,
            ttl_seconds=ttl_seconds or self.default_ttl
        )
        await self.add_revocation(entry)
    
    async def revoke_org_tokens(self, org_id: str, reason: str, revoked_by: str, ttl_seconds: int = None):
        """Revoke all tokens for an organization."""
        entry = RevocationEntry(
            scope=RevocationScope.ORG,
            target_id=org_id,
            reason=reason,
            timestamp=time.time(),
            revoked_by=revoked_by,
            ttl_seconds=ttl_seconds or self.default_ttl
        )
        await self.add_revocation(entry)
    
    async def revoke_role_tokens(self, role: str, reason: str, revoked_by: str, ttl_seconds: int = None):
        """Revoke all tokens for a specific role."""
        entry = RevocationEntry(
            scope=RevocationScope.ROLE,
            target_id=role,
            reason=reason,
            timestamp=time.time(),
            revoked_by=revoked_by,
            ttl_seconds=ttl_seconds or self.default_ttl
        )
        await self.add_revocation(entry)
    
    async def revoke_all_tokens(self, reason: str, revoked_by: str, ttl_seconds: int = None):
        """Revoke all tokens globally."""
        entry = RevocationEntry(
            scope=RevocationScope.GLOBAL,
            target_id="*",
            reason=reason,
            timestamp=time.time(),
            revoked_by=revoked_by,
            ttl_seconds=ttl_seconds or self.default_ttl
        )
        await self.add_revocation(entry)
    
    async def cleanup_expired(self) -> int:
        """Clean up expired revocation entries."""
        async def keys_operation(redis_client, pattern):
            return await redis_client.keys(pattern)
        
        keys = await redis_failure_handler.execute_with_fallback(keys_operation, f"{self.key_prefix}*")
        cleaned = 0
        
        for key in keys:
            entry = await self.get_revocation_by_key(key)
            if entry and entry.is_expired():
                await self.delete_revocation_by_key(key)
                cleaned += 1
        
        return cleaned
    
    async def get_revocation_by_key(self, key: str) -> Optional[RevocationEntry]:
        """Get revocation entry by Redis key."""
        async def get_operation(redis_client, k):
            return await redis_client.get(k)
        
        data = await redis_failure_handler.execute_with_fallback(get_operation, key)
        if not data:
            return None
        
        try:
            entry_data = json.loads(data)
            return RevocationEntry.from_dict(entry_data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None
    
    async def delete_revocation_by_key(self, key: str) -> bool:
        """Delete revocation entry by key."""
        async def delete_operation(redis_client, k):
            result = await redis_client.delete(k)
            return result > 0
        
        return await redis_failure_handler.execute_with_fallback(delete_operation, key)
    
    async def get_revocation_status(self) -> Dict[str, Any]:
        async def keys_operation(redis_client, pattern):
            return await redis_client.keys(pattern)
        
        async def get_operation(redis_client, k):
            return await redis_client.get(k)
        
        # Count revocations by scope
        scope_counts = {scope.value: 0 for scope in RevocationScope}
        
        keys = await redis_failure_handler.execute_with_fallback(keys_operation, f"{self.key_prefix}*")
        for key in keys:
            parts = key.split(":")
            if len(parts) >= 2:
                scope = parts[1]
                if scope in scope_counts:
                    scope_counts[scope] += 1
        
        # Get global revocation timestamp
        global_timestamp = await redis_failure_handler.execute_with_fallback(
            get_operation, 
            f"{self.global_key}:timestamp"
        )
        
        return {
            "total_revocations": len(keys),
            "revocations_by_scope": scope_counts,
            "global_revocation_time": float(global_timestamp) if global_timestamp else None,
            "store_type": "redis",
            "redis_status": redis_failure_handler.get_status()
        }

# Global revocation store instance
revocation_store = RevocationStore()
