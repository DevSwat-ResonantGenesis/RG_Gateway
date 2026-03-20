"""
User Memory Routes - Memory Panel gateway endpoints.

Routes to the REAL memory_service which uses:
- ResonanceHasher for proper hash coordinates
- PCA-based XYZ coordinate calculation from embeddings
- NeuralGravityEngine for cluster-based retrieval
- Proper semantic proximity scoring
"""
from fastapi import APIRouter, Request, HTTPException, Header
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import httpx

router = APIRouter(prefix="/memory-panel", tags=["memory-panel"])

# Route to the REAL memory service with proper ResonanceHasher
MEMORY_SERVICE_URL = "http://memory_service:8000"


async def proxy_request(method: str, path: str, headers: dict, data: dict = None):
    """Proxy request to Memory service with user context."""
    try:
        async with httpx.AsyncClient() as client:
            if method == "GET":
                resp = await client.get(
                    f"{MEMORY_SERVICE_URL}{path}",
                    headers=headers,
                    timeout=30.0
                )
            elif method == "POST":
                resp = await client.post(
                    f"{MEMORY_SERVICE_URL}{path}",
                    headers=headers,
                    json=data,
                    timeout=60.0
                )
            elif method == "PATCH":
                resp = await client.patch(
                    f"{MEMORY_SERVICE_URL}{path}",
                    headers=headers,
                    json=data,
                    timeout=30.0
                )
            else:
                raise HTTPException(status_code=405, detail="Method not allowed")
            
            return resp.json()
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Memory service timeout")
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Memory service unavailable: {str(e)}")


def get_user_headers(request: Request) -> dict:
    """Extract user context headers from request."""
    return {
        "X-User-Id": request.headers.get("X-User-Id", ""),
        "X-Org-Id": request.headers.get("X-Org-Id", ""),
        "Authorization": request.headers.get("Authorization", "")
    }


# Health & Status
@router.get("/health")
async def health():
    """Memory service health check."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEMORY_SERVICE_URL}/health", timeout=5.0)
            return resp.json()
    except:
        return {"status": "unavailable"}


@router.get("/status")
async def status():
    """Get Memory service status."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MEMORY_SERVICE_URL}/health", timeout=5.0)
            return resp.json()
    except:
        return {"status": "unavailable"}


# Memory Embedding & Retrieval
class EmbedRequest(BaseModel):
    content: str
    memory_type: str = "text"
    title: Optional[str] = None
    tags: List[str] = []
    cluster: str = "default"
    layer: str = "active"
    metadata: Dict[str, Any] = {}
    source: Optional[str] = None


class RetrieveRequest(BaseModel):
    query: str
    top_k: int = 10
    cluster: Optional[str] = None
    layer: Optional[str] = None
    min_similarity: float = 0.0
    include_embeddings: bool = False


@router.post("/memories/embed")
async def embed_memory(request: Request, body: EmbedRequest):
    """
    Embed and store a new memory using ResonanceHasher.
    Coordinates are calculated using PCA on the embedding vector.
    """
    headers = get_user_headers(request)
    # Map to memory_service RAG endpoint
    rag_body = {
        "content": body.content,
        "metadata": {
            "title": body.title,
            "tags": body.tags,
            "cluster": body.cluster,
            "layer": body.layer,
            "memory_type": body.memory_type,
            **body.metadata
        }
    }
    return await proxy_request("POST", "/rag/memories", headers, rag_body)


@router.post("/memories/retrieve")
async def retrieve_memories(request: Request, body: RetrieveRequest):
    """
    Retrieve similar memories using semantic search.
    Uses ResonanceHasher proximity scoring and NeuralGravityEngine.
    """
    headers = get_user_headers(request)
    # Map to memory_service RAG search endpoint
    search_body = {
        "query": body.query,
        "top_k": body.top_k,
        "min_similarity": body.min_similarity
    }
    return await proxy_request("POST", "/rag/search", headers, search_body)


@router.get("/memories")
async def list_memories(
    request: Request,
    cluster: Optional[str] = None,
    layer: Optional[str] = None,
    limit: int = 100
):
    """List all memories with real XYZ coordinates."""
    headers = get_user_headers(request)
    path = f"/rag/memories?limit={limit}"
    if cluster:
        path += f"&cluster={cluster}"
    if layer:
        path += f"&layer={layer}"
    return await proxy_request("GET", path, headers)


@router.get("/memories/{memory_id}")
async def get_memory(request: Request, memory_id: str):
    """Get a specific memory with XYZ coordinates."""
    headers = get_user_headers(request)
    return await proxy_request("GET", f"/rag/memories/{memory_id}", headers)


@router.patch("/memories/{memory_id}/archive")
async def archive_memory(request: Request, memory_id: str):
    """
    Archive a memory - move to archive layer.
    
    IMPORTANT: Memories can NEVER be deleted because they are interconnected
    in the hash universe. Deleting one would break the integrity of the entire
    system. Instead, memories can only be archived.
    """
    headers = get_user_headers(request)
    return await proxy_request("PATCH", f"/rag/memories/{memory_id}", headers, {"metadata": {"layer": "archive"}})


@router.patch("/memories/{memory_id}/restore")
async def restore_memory(request: Request, memory_id: str, layer: str = "active"):
    """Restore an archived memory to active or another layer."""
    headers = get_user_headers(request)
    return await proxy_request("PATCH", f"/rag/memories/{memory_id}", headers, {"metadata": {"layer": layer}})


# Clusters
class ClusterCreateRequest(BaseModel):
    name: str
    description: Optional[str] = None
    cluster_type: str = "custom"
    color: str = "#6366f1"


@router.post("/clusters")
async def create_cluster(request: Request, body: ClusterCreateRequest):
    """Create a new memory cluster."""
    headers = get_user_headers(request)
    return await proxy_request("POST", "/clusters", headers, body.model_dump())


@router.get("/clusters")
async def list_clusters(request: Request):
    """List all clusters."""
    headers = get_user_headers(request)
    return await proxy_request("GET", "/clusters", headers)


# Universe Visualization
@router.get("/universe")
async def get_universe(request: Request):
    """
    Get complete universe state for 3D visualization.
    Uses real XYZ coordinates from ResonanceHasher PCA calculation.
    """
    headers = get_user_headers(request)
    return await proxy_request("GET", "/rag/universe", headers)


# Stats & API Keys
@router.get("/stats")
async def get_stats(request: Request):
    """Get storage statistics."""
    headers = get_user_headers(request)
    return await proxy_request("GET", "/stats", headers)


@router.post("/api-keys")
async def create_api_key(request: Request, name: str = "default"):
    """Create an API key for external access."""
    headers = get_user_headers(request)
    return await proxy_request("POST", f"/api-keys?name={name}", headers, {})


# Timeline View
class TimelineRequest(BaseModel):
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    group_by: str = "day"  # day, week, month
    types: List[str] = []
    min_resonance: float = 0.0
    limit: int = 500


@router.post("/timeline")
async def get_timeline(request: Request, body: TimelineRequest):
    """
    Get memories grouped by time for timeline visualization.
    Returns memories sorted by date with grouping.
    """
    headers = get_user_headers(request)
    return await proxy_request("POST", "/rag/timeline", headers, body.model_dump())


# Advanced Search
class AdvancedSearchRequest(BaseModel):
    query: str = ""
    types: List[str] = []
    min_resonance: float = 0.0
    max_resonance: float = 1.0
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    clusters: List[str] = []
    sort_by: str = "relevance"  # relevance, date, resonance, importance
    limit: int = 100


@router.post("/memories/search/advanced")
async def advanced_search(request: Request, body: AdvancedSearchRequest):
    """
    Advanced memory search with multiple filters.
    Supports filtering by type, resonance, date range, and clusters.
    """
    headers = get_user_headers(request)
    return await proxy_request("POST", "/rag/search/advanced", headers, body.model_dump())


# Resonance Network
@router.get("/network")
async def get_resonance_network(
    request: Request,
    min_resonance: float = 0.5,
    max_edges: int = 200
):
    """
    Get resonance network for graph visualization.
    Returns nodes (memories) and edges (resonance connections).
    """
    headers = get_user_headers(request)
    path = f"/rag/network?min_resonance={min_resonance}&max_edges={max_edges}"
    return await proxy_request("GET", path, headers)


# Memory Health
@router.get("/health/detailed")
async def get_memory_health(request: Request):
    """
    Get detailed memory system health metrics.
    Includes invariant violations, orphaned memories, cluster fragmentation.
    """
    headers = get_user_headers(request)
    return await proxy_request("GET", "/health/detailed", headers)
