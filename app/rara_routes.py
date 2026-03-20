"""RARA Routes - Resident Autonomous Runtime Agent gateway endpoints."""
from fastapi import APIRouter, Request, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import os
import httpx

router = APIRouter(prefix="/rara", tags=["rara"])

RARA_SERVICE_URL = os.getenv("RARA_SERVICE_URL", "http://rg_internal_invarients_sim:8093")


def require_rara_admin(request: Request) -> None:
    role = getattr(request.state, "role", None)
    if role not in {"platform_owner", "platform_dev"}:
        raise HTTPException(status_code=403, detail="Admin privileges required")


async def proxy_get(path: str):
    """Proxy GET request to RARA service."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{RARA_SERVICE_URL}{path}", timeout=30.0)
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"RARA service unavailable: {str(e)}")


async def proxy_post(path: str, data: dict = None):
    """Proxy POST request to RARA service."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{RARA_SERVICE_URL}{path}",
                json=data,
                timeout=60.0
            )
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"RARA service unavailable: {str(e)}")


async def proxy_put(path: str, data: dict = None):
    """Proxy PUT request to RARA service."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{RARA_SERVICE_URL}{path}",
                json=data,
                timeout=60.0
            )
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"RARA service unavailable: {str(e)}")


async def proxy_delete(path: str):
    """Proxy DELETE request to RARA service."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(f"{RARA_SERVICE_URL}{path}", timeout=30.0)
            return resp.json()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"RARA service unavailable: {str(e)}")


# Health & Status
@router.get("/health")
async def health():
    """RARA health check."""
    return await proxy_get("/health")


@router.get("/status", dependencies=[Depends(require_rara_admin)])
async def status():
    """Get RARA system status."""
    return await proxy_get("/status")


# Agents
@router.get("/agents", dependencies=[Depends(require_rara_admin)])
async def list_agents():
    """List all registered RARA agents."""
    return await proxy_get("/agents")


@router.get("/agents/{agent_id}/capabilities", dependencies=[Depends(require_rara_admin)])
async def get_agent_capabilities(agent_id: str):
    """Get agent capabilities."""
    return await proxy_get(f"/agents/{agent_id}/capabilities")


@router.get("/agents/{agent_id}/stats", dependencies=[Depends(require_rara_admin)])
async def get_agent_stats(agent_id: str):
    """Get agent statistics."""
    return await proxy_get(f"/agents/{agent_id}/stats")


# Kill Switch
@router.get("/control/kill-switch/status", dependencies=[Depends(require_rara_admin)])
async def kill_switch_status():
    """Get kill switch status."""
    return await proxy_get("/control/kill-switch/status")


@router.get("/control/kill-switch/events", dependencies=[Depends(require_rara_admin)])
async def kill_switch_events(limit: int = 50):
    """Get kill switch events."""
    return await proxy_get(f"/control/kill-switch/events?limit={limit}")


@router.post("/control/freeze", dependencies=[Depends(require_rara_admin)])
async def freeze(actor: str = "gateway", reason: str = "Gateway freeze"):
    """Freeze system."""
    return await proxy_post(f"/control/freeze?actor={actor}&reason={reason}")


@router.post("/control/unfreeze", dependencies=[Depends(require_rara_admin)])
async def unfreeze(actor: str = "gateway", reason: str = "Gateway unfreeze"):
    """Unfreeze system."""
    return await proxy_post(f"/control/unfreeze?actor={actor}&reason={reason}")


@router.post("/control/emergency-stop", dependencies=[Depends(require_rara_admin)])
async def emergency_stop(actor: str = "gateway", reason: str = "Emergency stop"):
    """Emergency stop."""
    return await proxy_post(f"/control/emergency-stop?actor={actor}&reason={reason}")


class EmergencyResetRequest(BaseModel):
    actor: str
    reason: str
    confirmation_token: str


@router.post("/control/emergency-reset", dependencies=[Depends(require_rara_admin)])
async def emergency_reset(request: EmergencyResetRequest):
    """Reset from emergency stop."""
    return await proxy_post("/control/emergency-reset", request.model_dump())


@router.post("/control/reset-budgets", dependencies=[Depends(require_rara_admin)])
async def reset_budgets():
    """Reset daily budgets."""
    return await proxy_post("/control/reset-budgets")


# Snapshots
@router.get("/snapshots", dependencies=[Depends(require_rara_admin)])
async def list_snapshots(limit: int = 20):
    """List snapshots."""
    return await proxy_get(f"/snapshots?limit={limit}")


@router.get("/snapshots/{snapshot_id}", dependencies=[Depends(require_rara_admin)])
async def get_snapshot(snapshot_id: str):
    """Get snapshot details."""
    return await proxy_get(f"/snapshots/{snapshot_id}")


@router.post("/snapshots/create", dependencies=[Depends(require_rara_admin)])
async def create_snapshot(trigger: str = "gateway"):
    """Create snapshot."""
    return await proxy_post(f"/snapshots/create?trigger={trigger}")


@router.post("/snapshots/{snapshot_id}/restore", dependencies=[Depends(require_rara_admin)])
async def restore_snapshot(snapshot_id: str, reason: str = "Gateway restore"):
    """Restore to snapshot."""
    return await proxy_post(f"/snapshots/{snapshot_id}/restore?reason={reason}")


# Mutations
class MutationRequest(BaseModel):
    actor: str
    capability: str
    target: str
    operation: dict
    preconditions: Optional[List[dict]] = None
    postconditions: Optional[List[dict]] = None
    rationale: str
    confidence: float


@router.post("/mutations/execute", dependencies=[Depends(require_rara_admin)])
async def execute_mutation(agent_id: str, mutation: MutationRequest):
    """Execute a mutation."""
    return await proxy_post(f"/mutations/execute?agent_id={agent_id}", mutation.model_dump())


@router.get("/mutations/pending", dependencies=[Depends(require_rara_admin)])
async def pending_mutations():
    """Get pending mutations."""
    return await proxy_get("/mutations/pending")


class ApprovalRequest(BaseModel):
    mutation_id: str
    approval_token: str = "human-approved"


@router.post("/mutations/approve", dependencies=[Depends(require_rara_admin)])
async def approve_mutation(request: ApprovalRequest):
    """Approve a mutation."""
    return await proxy_post("/mutations/approve", request.model_dump())


class RejectRequest(BaseModel):
    mutation_id: str
    reason: str = "Rejected"


@router.post("/mutations/reject", dependencies=[Depends(require_rara_admin)])
async def reject_mutation(request: RejectRequest):
    """Reject a mutation."""
    return await proxy_post("/mutations/reject", request.model_dump())


@router.get("/mutations/log", dependencies=[Depends(require_rara_admin)])
async def mutation_log(limit: int = 50):
    """Get mutation log."""
    return await proxy_get(f"/mutations/log?limit={limit}")


# Agents
class AgentRegistration(BaseModel):
    agent_id: str
    role: str
    dsid: str
    public_key: str


@router.post("/agents/register", dependencies=[Depends(require_rara_admin)])
async def register_agent(registration: AgentRegistration):
    """Register an agent."""
    return await proxy_post("/agents/register", registration.model_dump())


@router.get("/agents/{agent_id}/capabilities", dependencies=[Depends(require_rara_admin)])
async def agent_capabilities(agent_id: str):
    """Get agent capabilities."""
    return await proxy_get(f"/agents/{agent_id}/capabilities")


class AddCapabilityRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "custom"
    enabled: bool = True
    required_permissions: List[str] = []
    # Enhanced fields
    capability_type: str = "action"  # action, tool, integration, workflow
    execution_mode: str = "sync"  # sync, async, streaming
    rate_limit: Optional[int] = None
    timeout: int = 30
    retry_policy: str = "none"  # none, linear, exponential
    max_retries: int = 3
    input_schema: Optional[dict] = None
    output_schema: Optional[dict] = None
    webhook_url: Optional[str] = None
    api_endpoint: Optional[str] = None
    auth_type: str = "none"  # none, api_key, oauth2, bearer
    cost_per_call: float = 0.0
    tags: List[str] = []


@router.post("/agents/{agent_id}/capabilities", dependencies=[Depends(require_rara_admin)])
async def add_agent_capability(agent_id: str, capability: AddCapabilityRequest):
    """Add a custom capability to an agent."""
    return await proxy_post(f"/agents/{agent_id}/capabilities", capability.model_dump())


@router.put("/agents/{agent_id}/capabilities/{capability_id}", dependencies=[Depends(require_rara_admin)])
async def update_agent_capability(agent_id: str, capability_id: str, capability: AddCapabilityRequest):
    """Update an agent capability."""
    return await proxy_put(f"/agents/{agent_id}/capabilities/{capability_id}", capability.model_dump())


@router.delete("/agents/{agent_id}/capabilities/{capability_id}", dependencies=[Depends(require_rara_admin)])
async def delete_agent_capability(agent_id: str, capability_id: str):
    """Delete an agent capability."""
    return await proxy_delete(f"/agents/{agent_id}/capabilities/{capability_id}")


@router.post("/agents/{agent_id}/capabilities/{capability_id}/toggle", dependencies=[Depends(require_rara_admin)])
async def toggle_agent_capability(agent_id: str, capability_id: str, enabled: bool = True):
    """Toggle an agent capability on/off."""
    return await proxy_post(f"/agents/{agent_id}/capabilities/{capability_id}/toggle?enabled={enabled}")


@router.get("/agents/{agent_id}/stats", dependencies=[Depends(require_rara_admin)])
async def agent_stats(agent_id: str):
    """Get agent statistics."""
    return await proxy_get(f"/agents/{agent_id}/stats")


# Coordination
@router.get("/coordination/stats", dependencies=[Depends(require_rara_admin)])
async def coordination_stats():
    """Get coordination statistics."""
    return await proxy_get("/coordination/stats")


@router.get("/proposals/pending", dependencies=[Depends(require_rara_admin)])
async def pending_proposals():
    """Get pending proposals."""
    return await proxy_get("/proposals/pending")


# Invariants
@router.post("/invariants/check", dependencies=[Depends(require_rara_admin)])
async def check_invariants():
    """Run invariant checks."""
    return await proxy_post("/invariants/check")


@router.get("/invariants/results", dependencies=[Depends(require_rara_admin)])
async def invariant_results():
    """Get invariant results."""
    return await proxy_get("/invariants/results")


@router.get("/invariants/definitions", dependencies=[Depends(require_rara_admin)])
async def invariant_definitions():
    """Get invariant definitions."""
    return await proxy_get("/invariants/definitions")


@router.get("/invariants/summary", dependencies=[Depends(require_rara_admin)])
async def invariant_summary():
    """Get invariant summary."""
    return await proxy_get("/invariants/summary")


# Compliance
@router.get("/compliance/policy", dependencies=[Depends(require_rara_admin)])
async def compliance_policy():
    """Get compliance policy."""
    return await proxy_get("/compliance/policy")


@router.get("/compliance/policies", dependencies=[Depends(require_rara_admin)])
async def all_policies():
    """Get all policies."""
    return await proxy_get("/compliance/policies")


@router.get("/compliance/requirements/eu-ai-act", dependencies=[Depends(require_rara_admin)])
async def eu_ai_act_requirements():
    """Get EU AI Act requirements."""
    return await proxy_get("/compliance/requirements/eu-ai-act")


@router.get("/compliance/requirements/soc2", dependencies=[Depends(require_rara_admin)])
async def soc2_controls():
    """Get SOC2 controls."""
    return await proxy_get("/compliance/requirements/soc2")


@router.get("/compliance/report", dependencies=[Depends(require_rara_admin)])
async def compliance_report():
    """Get compliance report."""
    return await proxy_get("/compliance/report")


@router.get("/compliance/audit-trail", dependencies=[Depends(require_rara_admin)])
async def audit_trail():
    """Get audit trail."""
    return await proxy_get("/compliance/audit-trail")


# Governance
@router.get("/governance/state", dependencies=[Depends(require_rara_admin)])
async def governance_state():
    """Get governance state."""
    return await proxy_get("/governance/state")


@router.post("/governance/explain", dependencies=[Depends(require_rara_admin)])
async def generate_explanation(mutation: MutationRequest):
    """Generate explainability artifact."""
    return await proxy_post("/governance/explain", mutation.model_dump())
