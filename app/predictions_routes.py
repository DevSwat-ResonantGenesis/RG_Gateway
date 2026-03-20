"""Predictions API Routes.

Proxies prediction requests to the V8 Engine API service.
The HashSphere frontend calls POST /predict with {hash, words, text} payloads.
The /ml/predictions endpoint returns prediction history from V8.
"""

import logging
from fastapi import APIRouter, Request

from .reverse_proxy import proxy

logger = logging.getLogger(__name__)

router = APIRouter(tags=["predictions"])


@router.post("/predict")
async def make_prediction(request: Request):
    """Proxy prediction to V8 Engine.
    
    Accepts:
      - {hash: "0x..."} → V8 predicts 12 words from hash
      - {words: "word1 word2 ..."} → V8 predicts hash from words
      - {text: "..."} → treated as words input
    """
    return await proxy("v8-api", "api/predict", request)


@router.get("/ml/predictions")
async def list_predictions(request: Request):
    """Get prediction history from V8 Engine."""
    return await proxy("v8-api", "api/admin/predictions", request)


@router.get("/ml/predictions/{prediction_id}")
async def get_prediction(prediction_id: str, request: Request):
    """Get a specific prediction from V8 Engine."""
    return await proxy("v8-api", f"api/admin/predictions/{prediction_id}", request)


@router.get("/predictions/evidence/{prediction_id}")
async def get_prediction_evidence(prediction_id: str, request: Request):
    """Get evidence for a prediction from V8 Engine."""
    return await proxy("v8-api", f"api/admin/predictions/{prediction_id}", request)


@router.get("/hash-sphere/health")
async def hash_sphere_health():
    """Hash Sphere health check — aggregates memory + V8 status."""
    import httpx
    mem_ok = False
    v8_ok = False
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get("http://memory_service:8000/health")
            mem_ok = r.status_code == 200
    except Exception:
        pass
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get("http://v8_api_service:8080/")
            v8_ok = r.status_code == 200
    except Exception:
        pass
    return {
        "status": "healthy" if (mem_ok and v8_ok) else ("degraded" if (mem_ok or v8_ok) else "offline"),
        "service": "hash-sphere",
        "version": "8.4",
        "memory_service": "ok" if mem_ok else "offline",
        "v8_engine": "ok" if v8_ok else "offline",
    }
