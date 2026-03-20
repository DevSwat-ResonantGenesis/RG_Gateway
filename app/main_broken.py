import sys
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx
from gateway.app.auth_proxy import proxy_auth

# Deterministic sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Single service entrypoint
app = FastAPI(
    title="Gateway Service",
    description="API Gateway for Genesis2026",
    version="1.0.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Health check endpoint
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "gateway"}

# Root endpoint
@app.get("/")
async def root():
    return {"message": "Gateway Service is running"}

# Auth Service Proxy
@app.api_route("/api/v1/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def proxy_auth(path: str, request: Request):
    """Proxy requests to auth service"""
    auth_service_url = "http://localhost:8001"
    
    # Build the full URL
    url = f"{auth_service_url}/{path}"
    
    # Get request data
    headers = dict(request.headers)
    headers.pop("host", None)  # Remove host header
    
    # Make request to auth service
    async with httpx.AsyncClient() as client:
        try:
            if request.method == "GET":
                response = await client.get(url, headers=headers, params=request.query_params)
            elif request.method == "POST":
                body = await request.body()
                response = await client.post(url, headers=headers, content=body)
            elif request.method == "PUT":
                body = await request.body()
                response = await client.put(url, headers=headers, content=body)
            elif request.method == "DELETE":
                response = await client.delete(url, headers=headers)
            
            # Return response
            return Response(
                content=response.content,
                status_code=response.status_code,
                headers=dict(response.headers)
            )
        except Exception as e:
            return {"error": f"Auth service error: {str(e)}"}, 500

# Auth health endpoint through gateway
@app.get("/api/v1/auth/health")
async def auth_health():
    """Auth service health through gateway"""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get("http://localhost:8001/health")
            return response.json()
    except Exception as e:
        return {"error": f"Auth service unavailable: {str(e)}"}, 503

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
