"""Policies API Routes.

These endpoints provide policy management functionality.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter(prefix="/policies", tags=["policies"])


# ============================================
# Request Models
# ============================================

class PolicyCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    rules: List[Dict[str, Any]]
    enabled: bool = True


class PolicyUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    rules: Optional[List[Dict[str, Any]]] = None
    enabled: Optional[bool] = None


# ============================================
# Policy Endpoints
# ============================================

@router.get("")
async def list_policies(request: Request):
    """List all policies."""
    return {
        "policies": [
            {
                "id": str(uuid4()),
                "name": "Default Rate Limit",
                "description": "Default rate limiting policy",
                "rules": [{"type": "rate_limit", "limit": 1000, "window": "hour"}],
                "enabled": True,
                "created_at": datetime.now().isoformat(),
            },
            {
                "id": str(uuid4()),
                "name": "Content Filter",
                "description": "Content filtering policy",
                "rules": [{"type": "content_filter", "blocked_terms": []}],
                "enabled": True,
                "created_at": datetime.now().isoformat(),
            },
        ],
        "total": 2,
    }


@router.post("")
async def create_policy(
    payload: PolicyCreateRequest,
    request: Request,
):
    """Create a new policy."""
    return {
        "id": str(uuid4()),
        "name": payload.name,
        "description": payload.description,
        "rules": payload.rules,
        "enabled": payload.enabled,
        "created_at": datetime.now().isoformat(),
    }


@router.put("/{policy_id}")
async def update_policy(
    policy_id: str,
    payload: PolicyUpdateRequest,
    request: Request,
):
    """Update a policy."""
    return {
        "id": policy_id,
        "name": payload.name or "Updated Policy",
        "description": payload.description,
        "rules": payload.rules or [],
        "enabled": payload.enabled if payload.enabled is not None else True,
        "updated_at": datetime.now().isoformat(),
    }


@router.delete("/{policy_id}")
async def delete_policy(policy_id: str, request: Request):
    """Delete a policy."""
    return {
        "id": policy_id,
        "deleted": True,
        "deleted_at": datetime.now().isoformat(),
    }
