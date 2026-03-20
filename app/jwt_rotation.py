"""JWT token rotation and refresh handling."""

import hashlib
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

import httpx
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from .config import settings


@dataclass
class TokenInfo:
    """Information about a token."""
    token_hash: str
    user_id: str
    issued_at: datetime
    expires_at: datetime
    refresh_token_hash: Optional[str] = None
    revoked: bool = False
    device_id: Optional[str] = None
    ip_address: Optional[str] = None


@dataclass
class KeyVersion:
    """JWT signing key version."""
    version: int
    key: str
    created_at: datetime
    expires_at: datetime
    is_active: bool = True


class JWTRotationManager:
    """Manages JWT token rotation and key versioning."""

    def __init__(self):
        # Active tokens (in production, use Redis)
        self.active_tokens: Dict[str, TokenInfo] = {}
        # Revoked tokens (blacklist)
        self.revoked_tokens: Set[str] = set()
        # Refresh tokens
        self.refresh_tokens: Dict[str, str] = {}  # refresh_hash -> access_hash
        # Key versions for rotation
        self.key_versions: List[KeyVersion] = []
        self.current_key_version: int = 1
        
        # Token settings
        self.access_token_ttl = timedelta(minutes=15)
        self.refresh_token_ttl = timedelta(days=7)
        self.key_rotation_interval = timedelta(days=30)
        
        # Initialize first key
        self._initialize_keys()

    def _initialize_keys(self):
        """Initialize signing keys."""
        now = datetime.utcnow()
        key = secrets.token_hex(32)
        self.key_versions.append(KeyVersion(
            version=1,
            key=key,
            created_at=now,
            expires_at=now + self.key_rotation_interval,
        ))

    def _hash_token(self, token: str) -> str:
        """Hash a token for storage."""
        return hashlib.sha256(token.encode()).hexdigest()

    def rotate_keys(self) -> KeyVersion:
        """Rotate to a new signing key."""
        now = datetime.utcnow()
        
        # Mark old keys as inactive (but keep for verification)
        for key in self.key_versions:
            if key.expires_at < now:
                key.is_active = False

        # Create new key
        self.current_key_version += 1
        new_key = KeyVersion(
            version=self.current_key_version,
            key=secrets.token_hex(32),
            created_at=now,
            expires_at=now + self.key_rotation_interval,
        )
        self.key_versions.append(new_key)

        # Keep only last 3 key versions
        if len(self.key_versions) > 3:
            self.key_versions = self.key_versions[-3:]

        return new_key

    def get_current_key(self) -> KeyVersion:
        """Get current active signing key."""
        for key in reversed(self.key_versions):
            if key.is_active:
                return key
        # If no active key, rotate
        return self.rotate_keys()

    def register_token(
        self,
        access_token: str,
        user_id: str,
        refresh_token: Optional[str] = None,
        device_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> TokenInfo:
        """Register a new token pair."""
        now = datetime.utcnow()
        access_hash = self._hash_token(access_token)
        refresh_hash = self._hash_token(refresh_token) if refresh_token else None

        token_info = TokenInfo(
            token_hash=access_hash,
            user_id=user_id,
            issued_at=now,
            expires_at=now + self.access_token_ttl,
            refresh_token_hash=refresh_hash,
            device_id=device_id,
            ip_address=ip_address,
        )

        self.active_tokens[access_hash] = token_info
        if refresh_hash:
            self.refresh_tokens[refresh_hash] = access_hash

        return token_info

    def is_token_valid(self, token: str) -> bool:
        """Check if token is valid and not revoked."""
        token_hash = self._hash_token(token)
        
        # Check blacklist
        if token_hash in self.revoked_tokens:
            return False

        # Check active tokens
        token_info = self.active_tokens.get(token_hash)
        if not token_info:
            return True  # Not tracked, let auth service validate

        # Check expiration
        if token_info.expires_at < datetime.utcnow():
            return False

        # Check if revoked
        if token_info.revoked:
            return False

        return True

    def revoke_token(self, token: str) -> bool:
        """Revoke a token."""
        token_hash = self._hash_token(token)
        
        # Add to blacklist
        self.revoked_tokens.add(token_hash)

        # Mark as revoked in active tokens
        if token_hash in self.active_tokens:
            self.active_tokens[token_hash].revoked = True
            
            # Also revoke associated refresh token
            refresh_hash = self.active_tokens[token_hash].refresh_token_hash
            if refresh_hash:
                self.revoked_tokens.add(refresh_hash)
                if refresh_hash in self.refresh_tokens:
                    del self.refresh_tokens[refresh_hash]

        return True

    def revoke_all_user_tokens(self, user_id: str) -> int:
        """Revoke all tokens for a user."""
        count = 0
        for token_hash, info in list(self.active_tokens.items()):
            if info.user_id == user_id:
                self.revoked_tokens.add(token_hash)
                info.revoked = True
                count += 1
                
                if info.refresh_token_hash:
                    self.revoked_tokens.add(info.refresh_token_hash)
                    if info.refresh_token_hash in self.refresh_tokens:
                        del self.refresh_tokens[info.refresh_token_hash]

        return count

    def can_refresh(self, refresh_token: str) -> bool:
        """Check if refresh token can be used."""
        refresh_hash = self._hash_token(refresh_token)
        
        if refresh_hash in self.revoked_tokens:
            return False

        if refresh_hash not in self.refresh_tokens:
            return True  # Not tracked, let auth service handle

        access_hash = self.refresh_tokens[refresh_hash]
        token_info = self.active_tokens.get(access_hash)
        
        if not token_info:
            return True

        # Check if refresh token expired (7 days from access token issue)
        refresh_expires = token_info.issued_at + self.refresh_token_ttl
        if refresh_expires < datetime.utcnow():
            return False

        return True

    def cleanup_expired(self):
        """Clean up expired tokens."""
        now = datetime.utcnow()
        
        # Clean active tokens
        expired_hashes = [
            h for h, info in self.active_tokens.items()
            if info.expires_at < now - timedelta(hours=1)  # Keep for 1 hour after expiry
        ]
        for h in expired_hashes:
            del self.active_tokens[h]

        # Clean revoked tokens older than 24 hours
        # In production, use TTL in Redis
        if len(self.revoked_tokens) > 10000:
            # Simple cleanup - remove oldest half
            self.revoked_tokens = set(list(self.revoked_tokens)[-5000:])

    def get_user_sessions(self, user_id: str) -> List[Dict[str, Any]]:
        """Get all active sessions for a user."""
        sessions = []
        for token_hash, info in self.active_tokens.items():
            if info.user_id == user_id and not info.revoked:
                if info.expires_at > datetime.utcnow():
                    sessions.append({
                        "device_id": info.device_id,
                        "ip_address": info.ip_address,
                        "issued_at": info.issued_at.isoformat(),
                        "expires_at": info.expires_at.isoformat(),
                    })
        return sessions


jwt_rotation_manager = JWTRotationManager()


class JWTRotationMiddleware(BaseHTTPMiddleware):
    """Middleware for JWT rotation and validation."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # Skip for non-authenticated paths
        if path in {"/", "/health", "/metrics"} or path.startswith("/api/auth"):
            return await call_next(request)

        auth_header = request.headers.get("authorization")
        if not auth_header or not auth_header.lower().startswith("bearer "):
            return await call_next(request)

        token = auth_header.split(" ", 1)[1].strip()

        # Check if token is revoked
        if not jwt_rotation_manager.is_token_valid(token):
            return JSONResponse(
                status_code=401,
                content={
                    "error": "token_revoked",
                    "message": "Token has been revoked or expired",
                },
            )

        response = await call_next(request)

        # Check if token is about to expire and add refresh hint
        token_hash = jwt_rotation_manager._hash_token(token)
        token_info = jwt_rotation_manager.active_tokens.get(token_hash)
        
        if token_info:
            time_left = (token_info.expires_at - datetime.utcnow()).total_seconds()
            if time_left < 300:  # Less than 5 minutes
                response.headers["X-Token-Refresh-Hint"] = "true"
                response.headers["X-Token-Expires-In"] = str(int(time_left))

        return response


# API endpoints for token management (to be added to gateway routers)
async def revoke_token_endpoint(token: str) -> Dict[str, Any]:
    """Revoke a specific token."""
    jwt_rotation_manager.revoke_token(token)
    return {"status": "revoked"}


async def revoke_all_tokens_endpoint(user_id: str) -> Dict[str, Any]:
    """Revoke all tokens for a user."""
    count = jwt_rotation_manager.revoke_all_user_tokens(user_id)
    return {"status": "revoked", "count": count}


async def get_sessions_endpoint(user_id: str) -> Dict[str, Any]:
    """Get all active sessions for a user."""
    sessions = jwt_rotation_manager.get_user_sessions(user_id)
    return {"sessions": sessions, "count": len(sessions)}


async def rotate_keys_endpoint() -> Dict[str, Any]:
    """Rotate JWT signing keys (admin only)."""
    new_key = jwt_rotation_manager.rotate_keys()
    return {
        "status": "rotated",
        "version": new_key.version,
        "expires_at": new_key.expires_at.isoformat(),
    }
