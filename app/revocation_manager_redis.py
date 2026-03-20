"""Global token revocation manager for system-wide invalidation with Redis persistence.

Author: Resonant Genesis Team
Updated: January 11, 2026
"""

import time
import asyncio
from typing import Set, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum

from .revocation_store import RevocationStore, RevocationScope, RevocationEntry

class RevocationManager:
    """Manages token revocation across the system with Redis persistence."""
    
    def __init__(self):
        self.store = RevocationStore()
        self.cleanup_interval = 300  # 5 minutes
        self.revocation_ttl = 3600  # 1 hour
        
    async def revoke_user_tokens(self, user_id: str, reason: str, revoked_by: str, ttl_seconds: int = None):
        """Revoke all tokens for a specific user."""
        try:
            await self.store.revoke_user_tokens(user_id, reason, revoked_by, ttl_seconds or self.revocation_ttl)
        except ConnectionError:
            # Redis is down, can't revoke - this is a security issue
            # In production, this should trigger an alert
            print(f"CRITICAL: Cannot revoke user {user_id} - Redis is down!")
            raise ConnectionError("Cannot revoke tokens - Redis unavailable")
        except Exception as e:
            print(f"Error revoking user tokens: {e}")
            raise
        
    async def revoke_org_tokens(self, org_id: str, reason: str, revoked_by: str, ttl_seconds: int = None):
        """Revoke all tokens for an organization."""
        try:
            await self.store.revoke_org_tokens(org_id, reason, revoked_by, ttl_seconds or self.revocation_ttl)
        except ConnectionError:
            print(f"CRITICAL: Cannot revoke org {org_id} - Redis is down!")
            raise ConnectionError("Cannot revoke tokens - Redis unavailable")
        except Exception as e:
            print(f"Error revoking org tokens: {e}")
            raise
        
    async def revoke_role_tokens(self, role: str, reason: str, revoked_by: str, ttl_seconds: int = None):
        """Revoke all tokens for a specific role."""
        try:
            await self.store.revoke_role_tokens(role, reason, revoked_by, ttl_seconds or self.revocation_ttl)
        except ConnectionError:
            print(f"CRITICAL: Cannot revoke role {role} - Redis is down!")
            raise ConnectionError("Cannot revoke tokens - Redis unavailable")
        except Exception as e:
            print(f"Error revoking role tokens: {e}")
            raise
        
    async def revoke_all_tokens(self, reason: str, revoked_by: str, ttl_seconds: int = None):
        """Revoke all tokens globally."""
        try:
            await self.store.revoke_all_tokens(reason, revoked_by, ttl_seconds or self.revocation_ttl)
        except ConnectionError:
            print(f"CRITICAL: Cannot revoke all tokens - Redis is down!")
            raise ConnectionError("Cannot revoke tokens - Redis unavailable")
        except Exception as e:
            print(f"Error revoking all tokens: {e}")
            raise
        
    async def is_token_revoked(self, token_claims: Dict[str, Any]) -> tuple[bool, Optional[str]]:
        """Check if a token is revoked."""
        try:
            return await self.store.is_token_revoked(token_claims)
        except ConnectionError:
            # Redis is down, assume token is NOT revoked (fail open for reads)
            # This is a security tradeoff - better to allow access than deny it
            # when the store is unavailable
            return False, "Redis unavailable - assuming token valid"
        except Exception as e:
            # Other errors, log and assume valid
            print(f"Error checking revocation: {e}")
            return False, "Revocation check failed - assuming token valid"
    
    async def cleanup_expired(self):
        """Clean up expired revocation entries."""
        return await self.store.cleanup_expired()
    
    async def get_revocation_status(self) -> Dict[str, Any]:
        """Get current revocation status."""
        return await self.store.get_revocation_status()

# Global revocation manager instance
revocation_manager = RevocationManager()
