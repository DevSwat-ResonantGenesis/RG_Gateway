"""Anchors Routes - Memory anchors and Hash Sphere anchors endpoints."""
from fastapi import APIRouter, Request
import httpx

router = APIRouter(prefix="/anchors", tags=["anchors"])


@router.get("")
async def list_anchors():
    """List all anchors."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "http://memory_service:8000/memory/hash-sphere/anchors",
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"anchors": [], "error": str(e)}


@router.post("")
async def create_anchor(anchor: dict):
    """Create an anchor."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "http://memory_service:8000/memory/hash-sphere/anchors",
                json=anchor,
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


@router.get("/{anchor_id}")
async def get_anchor(anchor_id: str):
    """Get anchor details."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"http://memory_service:8000/memory/hash-sphere/anchors/{anchor_id}",
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e)}


@router.delete("/{anchor_id}")
async def delete_anchor(anchor_id: str):
    """Delete an anchor."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"http://memory_service:8000/memory/hash-sphere/anchors/{anchor_id}",
                timeout=10.0
            )
            return resp.json()
    except Exception as e:
        return {"error": str(e)}
