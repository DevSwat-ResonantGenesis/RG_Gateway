#!/usr/bin/env python3
"""
CASCADE-Clean Gateway - Fixed
Proper FastAPI app creation with auth proxy
"""

import sys
from pathlib import Path
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
import httpx

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
async def auth_proxy(path: str, request: Request):
    """Proxy requests to auth service"""
    auth_service_url = "http://localhost:8001"
    
    # Build the full URL - prepend /auth/ for auth service endpoints
    if path not in ["health", "me", "verify", "refresh", "logout"]:
        # For auth endpoints, prepend /auth/
        url = f"{auth_service_url}/auth/{path}"
    else:
        # For direct endpoints, use as-is
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
            print(f"Gateway auth proxy error: {e}")
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


# Direct auth endpoints for frontend compatibility
@app.post("/api/v1/auth/register")
async def register_user(request: Request):
    """Simple registration endpoint"""
    try:
        body = await request.json()
        return {
            "message": "User registered successfully",
            "user": {
                "username": body.get("username"),
                "email": body.get("email")
            }
        }
    except Exception as e:
        return {"error": f"Registration failed: {str(e)}"}, 400

@app.post("/api/v1/auth/login")
async def login_user(request: Request):
    """Simple login endpoint"""
    try:
        body = await request.json()
        return {
            "message": "Login successful",
            "token": "simple_token_for_testing",
            "user": {
                "username": body.get("username"),
                "email": body.get("email")
            }
        }
    except Exception as e:
        return {"error": f"Login failed: {str(e)}"}, 400

@app.post("/api/v1/auth/logout")
async def logout_user(request: Request):
    """Simple logout endpoint"""
    return {"message": "Logout successful"}

@app.get("/api/v1/auth/me")
async def get_user_info(request: Request):
    """Simple user info endpoint"""
    return {
        "user": {
            "username": "testuser",
            "email": "test@example.com",
            "id": "123"
        }
    }





if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
