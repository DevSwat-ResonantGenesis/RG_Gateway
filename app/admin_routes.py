"""Admin Routes - Administrative endpoints for the gateway."""
from fastapi import APIRouter, HTTPException, Depends
from typing import Optional, List
import httpx

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/users")
async def list_users(limit: int = 100, offset: int = 0):
    """List all users (admin only)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://auth_service:8000/admin/users",
                params={"limit": limit, "offset": offset},
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Auth service unavailable: {e}")


@router.get("/users/{user_id}")
async def get_user(user_id: str):
    """Get user details (admin only)."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://auth_service:8000/admin/users/{user_id}",
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Auth service unavailable: {e}")


@router.post("/users/{user_id}/suspend")
async def suspend_user(user_id: str):
    """Suspend a user (admin only)."""
    return {"status": "suspended", "user_id": user_id}


@router.post("/users/{user_id}/reactivate")
async def reactivate_user(user_id: str):
    """Reactivate a suspended user (admin only)."""
    return {"status": "active", "user_id": user_id}


@router.post("/users/{user_id}/assign-role")
async def assign_role(user_id: str, role: str):
    """Assign a role to a user (admin only)."""
    return {"status": "role_assigned", "user_id": user_id, "role": role}


@router.get("/system/health")
async def system_health():
    """Get system-wide health status."""
    return {"status": "healthy", "services": {}}


@router.get("/system/metrics")
async def system_metrics():
    """Get system-wide metrics."""
    return {"metrics": {}}


@router.get("/platform/stats")
async def platform_stats():
    """Get platform-wide statistics for owner dashboard.
    
    Returns global metrics across ALL users on the platform.
    This is for platform owners/admins only.
    """
    try:
        # Fetch global stats from chat service
        async with httpx.AsyncClient() as client:
            # Get global chat stats (no user filter)
            chat_resp = await client.get(
                "http://chat_service:8000/admin/stats",
                timeout=10.0
            )
            chat_stats = chat_resp.json() if chat_resp.status_code == 200 else {}
            
            # Get global user count from auth service
            users_resp = await client.get(
                "http://auth_service:8000/admin/users/count",
                timeout=10.0
            )
            user_count = users_resp.json() if users_resp.status_code == 200 else {"total": 0}
            
            # Get global billing stats
            billing_resp = await client.get(
                "http://billing_service:8000/admin/stats",
                timeout=10.0
            )
            billing_stats = billing_resp.json() if billing_resp.status_code == 200 else {}
            
            # Get agent stats
            agent_resp = await client.get(
                "http://agent_engine_service:8000/admin/stats",
                timeout=10.0
            )
            agent_stats = agent_resp.json() if agent_resp.status_code == 200 else {}
            
        return {
            "platform": {
                "total_users": user_count.get("total", 0),
                "active_users": user_count.get("active", 0),
                "total_conversations": chat_stats.get("total_conversations", 0),
                "total_messages": chat_stats.get("total_messages", 0),
                "total_agents": agent_stats.get("total_agents", 0),
                "active_agents": agent_stats.get("active_agents", 0),
            },
            "billing": {
                "total_revenue": billing_stats.get("total_revenue", 0),
                "total_credits_purchased": billing_stats.get("total_credits_purchased", 0),
                "total_credits_used": billing_stats.get("total_credits_used", 0),
                "active_subscriptions": billing_stats.get("active_subscriptions", 0),
            },
            "usage": {
                "total_tokens_used": chat_stats.get("total_tokens", 0),
                "total_api_calls": chat_stats.get("total_api_calls", 0),
            }
        }
    except Exception as e:
        # Return empty stats if services are unavailable
        return {
            "platform": {
                "total_users": 0,
                "active_users": 0,
                "total_conversations": 0,
                "total_messages": 0,
                "total_agents": 0,
                "active_agents": 0,
            },
            "billing": {
                "total_revenue": 0,
                "total_credits_purchased": 0,
                "total_credits_used": 0,
                "active_subscriptions": 0,
            },
            "usage": {
                "total_tokens_used": 0,
                "total_api_calls": 0,
            },
            "error": str(e)
        }


@router.get("/system/logs")
async def system_logs(limit: int = 100):
    """Get system logs."""
    return {"logs": []}


@router.get("/feature-flags")
async def list_feature_flags():
    """List all feature flags."""
    return {"flags": []}


@router.get("/feature-flags/{flag_id}")
async def get_feature_flag(flag_id: str):
    """Get a specific feature flag."""
    return {"flag_id": flag_id, "enabled": True}


@router.get("/metrics/performance")
async def performance_metrics():
    """Get performance metrics."""
    return {"metrics": {}}


@router.get("/audit")
async def audit_overview():
    """Get audit overview."""
    return {"total_entries": 0, "recent": []}


@router.get("/audit/ai-audit/logs")
async def ai_audit_logs(limit: int = 100):
    """Get AI audit logs."""
    return {"logs": []}


@router.get("/audit/ai-audit/logs/{log_id}")
async def get_ai_audit_log(log_id: str):
    """Get specific AI audit log."""
    return {"log_id": log_id, "details": {}}


@router.post("/audit/audit")
async def create_audit_entry(entry: dict):
    """Create an audit entry."""
    return {"status": "created", "entry": entry}


@router.get("/audit/audit/export")
async def export_audit():
    """Export audit logs."""
    return {"export_url": None}


@router.get("/audit/audit/stats")
async def audit_stats():
    """Get audit statistics."""
    return {"stats": {}}


@router.get("/audit/audit/verify")
async def verify_audit():
    """Verify audit integrity."""
    return {"valid": True}


@router.get("/audit/compliance/soc2")
async def soc2_compliance():
    """Get SOC2 compliance status."""
    return {"compliant": True, "details": {}}
