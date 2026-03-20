"""Global token revocation manager for system-wide invalidation.

Author: Resonant Genesis Team
Updated: January 11, 2026
"""

import time
import asyncio
from typing import Set, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

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
    target_id: str  # user_id, org_id, or role
    reason: str
    timestamp: float
    revoked_by: str
    
    def is_expired(self, ttl_seconds: int = 3600) -> bool:
        """Check if revocation entry has expired."""
        return time.time() > (self.timestamp + ttl_seconds)

class RevocationManager:
    """Manages token revocation across the system."""
    
    def __init__(self):
        self.revocations: Dict[str, RevocationEntry] = {}
        self.global_revocation_time: Optional[float] = None
        self.cleanup_interval = 300  # 5 minutes
        self.revocation_ttl = 3600  # 1 hour
        
    def revoke_user_tokens(self, user_id: str, reason: str, revoked_by: str):
        """Revoke all tokens for a specific user."""
        key = f"user:{user_id}"
        entry = RevocationEntry(
            scope=RevocationScope.USER,
            target_id=user_id,
            reason=reason,
            timestamp=time.time(),
            revoked_by=revoked_by
        )
        self.revocations[key] = entry
        
    def revoke_org_tokens(self, org_id: str, reason: str, revoked_by: str):
        """Revoke all tokens for an organization."""
        key = f"org:{org_id}"
        entry = RevocationEntry(
            scope=RevocationScope.ORG,
            target_id=org_id,
            reason=reason,
            timestamp=time.time(),
            revoked_by=revoked_by
        )
        self.revocations[key] = entry
        
    def revoke_role_tokens(self, role: str, reason: str, revoked_by: str):
        """Revoke all tokens for a specific role."""
        key = f"role:{role}"
        entry = RevocationEntry(
            scope=RevocationScope.ROLE,
            target_id=role,
            reason=reason,
            timestamp=time.time(),
            revoked_by=revoked_by
        )
        self.revocations[key] = entry
        
    def revoke_all_tokens(self, reason: str, revoked_by: str):
        """Revoke all tokens globally."""
        self.global_revocation_time = time.time()
        
        # Add global revocation entry
        entry = RevocationEntry(
            scope=RevocationScope.GLOBAL,
            target_id="*",
            reason=reason,
            timestamp=time.time(),
            revoked_by=revoked_by
        )
        self.revocations["global"] = entry
        
    def is_token_revoked(self, token_data: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Check if a token is revoked based on its claims.
        
        Returns (is_revoked, reason).
        """
        # Check global revocation first
        if self.global_revocation_time:
            # Extract token issued time (iat claim)
            iat = token_data.get("iat")
            if iat and isinstance(iat, (int, float)):
                if float(iat) < self.global_revocation_time:
                    return True, "Global token revocation"
        
        # Check user revocation
        user_id = token_data.get("user_id")
        if user_id:
            user_key = f"user:{user_id}"
            if user_key in self.revocations:
                entry = self.revocations[user_key]
                if not entry.is_expired(self.revocation_ttl):
                    return True, f"User token revoked: {entry.reason}"
        
        # Check organization revocation
        org_id = token_data.get("org_id")
        if org_id:
            org_key = f"org:{org_id}"
            if org_key in self.revocations:
                entry = self.revocations[org_key]
                if not entry.is_expired(self.revocation_ttl):
                    return True, f"Organization token revoked: {entry.reason}"
        
        # Check role revocation
        role = token_data.get("role")
        if role:
            role_key = f"role:{role}"
            if role_key in self.revocations:
                entry = self.revocations[role_key]
                if not entry.is_expired(self.revocation_ttl):
                    return True, f"Role token revoked: {entry.reason}"
        
        return False, None
    
    def cleanup_expired(self):
        """Clean up expired revocation entries."""
        expired_keys = []
        current_time = time.time()
        
        for key, entry in self.revocations.items():
            if current_time > (entry.timestamp + self.revocation_ttl):
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.revocations[key]
    
    def get_revocation_status(self) -> Dict[str, Any]:
        """Get current revocation status."""
        return {
            "total_revocations": len(self.revocations),
            "global_revocation_time": self.global_revocation_time,
            "revocations_by_scope": {
                scope.value: len([r for r in self.revocations.values() if r.scope == scope])
                for scope in RevocationScope
            }
        }

# Global revocation manager instance
revocation_manager = RevocationManager()
