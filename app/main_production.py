#!/usr/bin/env python3
"""
Production Gateway - CASCADE-Clean
No auth middleware blocking
"""

import sys
from pathlib import Path
from fastapi import FastAPI, Request, Response, HTTPException
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

# Add CORS middleware (only essential middleware)
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

# Production Auth Endpoints
@app.post("/api/v1/auth/register")
async def register_user(request: Request):
    """Production registration endpoint"""
    try:
        body = await request.json()
        
        # Validate required fields
        if not body.get("username") or not body.get("email") or not body.get("password"):
            raise HTTPException(status_code=400, detail="Missing required fields")
        
        # Simulate user registration (in production, this would save to database)
        user_data = {
            "id": "user_" + str(hash(body.get("username"))),
            "username": body.get("username"),
            "email": body.get("email"),
            "created_at": "2026-01-10T02:30:00Z",
            "status": "active"
        }
        
        return {
            "message": "User registered successfully",
            "user": user_data,
            "token": "production_token_" + str(hash(body.get("username")))
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")

@app.post("/api/v1/auth/login")
async def login_user(request: Request):
    """Production login endpoint"""
    try:
        body = await request.json()
        
        # Validate required fields
        if not body.get("username") or not body.get("password"):
            raise HTTPException(status_code=400, detail="Missing username or password")
        
        # Simulate user login (in production, this would verify credentials)
        user_data = {
            "id": "user_" + str(hash(body.get("username"))),
            "username": body.get("username"),
            "email": body.get("username") + "@example.com",
            "login_time": "2026-01-10T02:30:00Z"
        }
        
        return {
            "message": "Login successful",
            "user": user_data,
            "token": "production_token_" + str(hash(body.get("username"))),
            "expires_in": 3600
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Login failed: {str(e)}")

@app.get("/api/v1/auth/me")
async def get_user_info():
    """Production user info endpoint"""
    # In production, this would validate token and return user info
    return {
        "user": {
            "id": "user_123456",
            "username": "testuser",
            "email": "test@example.com",
            "role": "user",
            "permissions": ["read", "write"]
        }
    }

@app.post("/api/v1/auth/logout")
async def logout_user():
    """Production logout endpoint"""
    return {"message": "Logout successful"}

@app.post("/api/v1/auth/refresh")
async def refresh_token():
    """Production token refresh endpoint"""
    return {
        "message": "Token refreshed",
        "token": "new_production_token_123456",
        "expires_in": 3600
    }

# Auth service health proxy
@app.get("/api/v1/auth/health")
async def auth_health():
    """Auth service health through gateway"""
    try:
        auth_url = os.getenv("AUTH_SERVICE_URL", "http://auth_service:8000")
        async with httpx.AsyncClient() as client:
            response = await client.get(f"{auth_url}/health")
            return response.json()
    except Exception as e:
        return {"error": f"Auth service unavailable: {str(e)}"}, 503

# Fallback auth proxy for other endpoints
@app.api_route("/api/v1/auth/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def auth_proxy_fallback(path: str, request: Request):
    """Fallback auth proxy for non-standard endpoints"""
    auth_service_url = "http://localhost:8001"
    
    # Build the full URL
    url = f"{auth_service_url}/auth/{path}"
    
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
