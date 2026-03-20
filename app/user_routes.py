"""User Management API Routes.

These endpoints provide user management, API keys, and service access functionality.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


router = APIRouter(tags=["users"])


# ============================================
# Request Models
# ============================================

class UserCreateRequest(BaseModel):
    email: str
    full_name: Optional[str] = None
    role: str = "user"


class UserUpdateRequest(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    status: Optional[str] = None


class ApiKeyCreateRequest(BaseModel):
    name: str
    expires_in_days: Optional[int] = None


class ApiKeyValidateRequest(BaseModel):
    api_key: str


class RevealSeedRequest(BaseModel):
    password: str


# ============================================
# User Endpoints
# ============================================

users_router = APIRouter(prefix="/users", tags=["users"])


@users_router.get("")
async def list_users(
    request: Request,
    limit: int = 50,
    offset: int = 0,
):
    """List all users."""
    return {
        "users": [],
        "total": 0,
        "limit": limit,
        "offset": offset,
    }


@users_router.post("")
async def create_user(payload: UserCreateRequest, request: Request):
    """Create a new user."""
    return {
        "id": str(uuid4()),
        "email": payload.email,
        "full_name": payload.full_name,
        "role": payload.role,
        "status": "active",
        "created_at": datetime.now().isoformat(),
    }


@users_router.get("/{user_id}")
async def get_user(user_id: str, request: Request):
    """Get user by ID."""
    return {
        "id": user_id,
        "email": "user@example.com",
        "full_name": "Example User",
        "role": "user",
        "status": "active",
        "created_at": datetime.now().isoformat(),
    }


@users_router.put("/{user_id}")
async def update_user(user_id: str, payload: UserUpdateRequest, request: Request):
    """Update a user."""
    return {
        "id": user_id,
        "email": payload.email or "user@example.com",
        "full_name": payload.full_name or "Example User",
        "status": payload.status or "active",
        "updated_at": datetime.now().isoformat(),
    }


# NOTE: Privileged operations (delete, suspend, reactivate, assign-role) 
# are ONLY available via /admin/users/* to enforce proper RBAC.
# /users/* is for scoped tenant operations only.


@users_router.post("/reveal-seed")
async def reveal_seed(payload: RevealSeedRequest, request: Request):
    """Reveal user's seed phrase (requires password)."""
    # In production, verify password and return actual seed
    return {
        "seed": "example seed phrase words here for demonstration only",
        "warning": "Never share your seed phrase with anyone",
    }


# ============================================
# User API Keys Endpoints (separate from org API keys)
# ============================================

user_router = APIRouter(prefix="/user", tags=["user"])


@user_router.get("/api-keys")
async def get_user_api_keys(request: Request):
    """Get current user's API keys (ResonantGenesis platform keys)."""
    user_id = request.headers.get("x-user-id")
    
    return {
        "keys": [
            {
                "id": str(uuid4()),
                "name": "Default Key",
                "prefix": "rg_****",
                "created_at": datetime.now().isoformat(),
                "last_used": datetime.now().isoformat(),
            }
        ]
    }


@user_router.post("/api-keys")
async def create_user_api_key(payload: ApiKeyCreateRequest, request: Request):
    """Create a new API key for current user."""
    import secrets
    
    key = f"rg_{secrets.token_urlsafe(32)}"
    
    return {
        "id": str(uuid4()),
        "name": payload.name,
        "key": key,
        "prefix": key[:8] + "****",
        "expires_at": (datetime.now() + timedelta(days=payload.expires_in_days)).isoformat() if payload.expires_in_days else None,
        "created_at": datetime.now().isoformat(),
    }


@user_router.delete("/api-keys/{key_id}")
async def delete_user_api_key(key_id: str, request: Request):
    """Delete a user API key."""
    return {
        "deleted": True,
        "key_id": key_id,
    }


@user_router.post("/api-keys/validate")
async def validate_api_key(payload: ApiKeyValidateRequest, request: Request):
    """Validate an API key."""
    # In production, actually validate the key
    is_valid = payload.api_key.startswith("rg_") and len(payload.api_key) > 10
    
    return {
        "valid": is_valid,
        "key_prefix": payload.api_key[:8] + "****" if len(payload.api_key) > 8 else "****",
    }


@user_router.get("/trial-status")
async def get_trial_status(request: Request):
    """Get current user's trial status."""
    user_id = request.headers.get("x-user-id")
    
    return {
        "is_trial": True,
        "trial_start": (datetime.now() - timedelta(days=7)).isoformat(),
        "trial_end": (datetime.now() + timedelta(days=7)).isoformat(),
        "days_remaining": 7,
        "features_available": ["chat", "agents", "memory"],
        "upgrade_url": "/billing/upgrade",
    }


@user_router.get("/service-access")
async def get_service_access(request: Request):
    """Check user's service access."""
    user_id = request.headers.get("x-user-id")
    
    return {
        "services": {
            "chat": {"enabled": True, "limit": 1000},
            "agents": {"enabled": True, "limit": 10},
            "memory": {"enabled": True, "limit": 10000},
            "teams": {"enabled": False, "requires": "pro"},
            "nft": {"enabled": False, "requires": "enterprise"},
        },
        "plan": "free",
        "can_upgrade": True,
    }


# ============================================
# User Preferences Endpoints
# ============================================

# In-memory storage for preferences (replace with database in production)
# Key: user_id, Value: preferences dict
_user_preferences_store: Dict[str, Dict[str, Any]] = {}

# Default preferences
DEFAULT_PREFERENCES = {
    "chat": {
        "auto_save": True,
        "show_timestamps": True,
        "show_provider_badges": True,
        "show_validity_scores": False,
        "compact_mode": False,
        "font_size": "medium",
        "input_auto_resize": True,
        "sound_notifications": False,
        "keyboard_shortcuts": True,
        "focus_highlights": True,
        "split_view": False,
        "split_width": 50,
    },
    "agent": {
        "selected_agent_hash": None,
        "selected_team_id": None,
        "agent_mode": False,
    },
    "display": {
        "theme": "dark",
        "sidebar_collapsed": False,
    },
}


class UserPreferencesUpdate(BaseModel):
    """Model for updating user preferences."""
    chat: Optional[Dict[str, Any]] = None
    agent: Optional[Dict[str, Any]] = None
    display: Optional[Dict[str, Any]] = None


@user_router.get("/preferences")
async def get_user_preferences(request: Request):
    """Get current user's preferences."""
    user_id = request.headers.get("x-user-id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    # Get stored preferences or return defaults
    stored = _user_preferences_store.get(user_id, {})
    
    # Merge with defaults (stored values override defaults)
    preferences = {
        "chat": {**DEFAULT_PREFERENCES["chat"], **stored.get("chat", {})},
        "agent": {**DEFAULT_PREFERENCES["agent"], **stored.get("agent", {})},
        "display": {**DEFAULT_PREFERENCES["display"], **stored.get("display", {})},
    }
    
    return {
        "preferences": preferences,
        "updated_at": stored.get("updated_at"),
    }


@user_router.put("/preferences")
async def update_user_preferences(payload: UserPreferencesUpdate, request: Request):
    """Update current user's preferences."""
    user_id = request.headers.get("x-user-id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    # Get existing preferences
    existing = _user_preferences_store.get(user_id, {
        "chat": {},
        "agent": {},
        "display": {},
    })
    
    # Update only provided fields
    if payload.chat:
        existing["chat"] = {**existing.get("chat", {}), **payload.chat}
    if payload.agent:
        existing["agent"] = {**existing.get("agent", {}), **payload.agent}
    if payload.display:
        existing["display"] = {**existing.get("display", {}), **payload.display}
    
    existing["updated_at"] = datetime.now().isoformat()
    
    # Store updated preferences
    _user_preferences_store[user_id] = existing
    
    # Return merged with defaults
    return {
        "preferences": {
            "chat": {**DEFAULT_PREFERENCES["chat"], **existing.get("chat", {})},
            "agent": {**DEFAULT_PREFERENCES["agent"], **existing.get("agent", {})},
            "display": {**DEFAULT_PREFERENCES["display"], **existing.get("display", {})},
        },
        "updated_at": existing["updated_at"],
    }


@user_router.patch("/preferences/{category}")
async def patch_user_preference_category(
    category: str,
    request: Request,
):
    """Patch a specific preference category."""
    user_id = request.headers.get("x-user-id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    if category not in ["chat", "agent", "display"]:
        raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    
    body = await request.json()
    
    # Get existing preferences
    existing = _user_preferences_store.get(user_id, {
        "chat": {},
        "agent": {},
        "display": {},
    })
    
    # Update the specific category
    existing[category] = {**existing.get(category, {}), **body}
    existing["updated_at"] = datetime.now().isoformat()
    
    # Store updated preferences
    _user_preferences_store[user_id] = existing
    
    return {
        "category": category,
        "values": {**DEFAULT_PREFERENCES[category], **existing[category]},
        "updated_at": existing["updated_at"],
    }


@user_router.delete("/preferences")
async def reset_user_preferences(request: Request):
    """Reset user preferences to defaults."""
    user_id = request.headers.get("x-user-id")
    
    if not user_id:
        raise HTTPException(status_code=401, detail="User ID required")
    
    # Remove stored preferences
    if user_id in _user_preferences_store:
        del _user_preferences_store[user_id]
    
    return {
        "reset": True,
        "preferences": DEFAULT_PREFERENCES,
    }
