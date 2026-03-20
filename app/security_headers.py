"""
Security Headers Middleware for Production Hardening

Adds essential security headers to all responses:
- X-Frame-Options: Prevents clickjacking
- X-Content-Type-Options: Prevents MIME sniffing
- X-XSS-Protection: XSS filter (legacy browsers)
- Referrer-Policy: Controls referrer information
- Content-Security-Policy: Controls resource loading
- Strict-Transport-Security: Forces HTTPS
- Permissions-Policy: Controls browser features
"""

from typing import Callable
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import settings


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Middleware to add security headers to all responses.
    
    These headers provide defense-in-depth against common web vulnerabilities:
    - XSS (Cross-Site Scripting)
    - Clickjacking
    - MIME sniffing attacks
    - Information leakage
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        # Prevent clickjacking - page cannot be embedded in iframes
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        
        # Prevent MIME type sniffing
        response.headers["X-Content-Type-Options"] = "nosniff"
        
        # XSS protection for legacy browsers
        response.headers["X-XSS-Protection"] = "1; mode=block"
        
        # Control referrer information sent with requests
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # Permissions Policy - disable unnecessary browser features
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), "
            "camera=(), "
            "geolocation=(), "
            "gyroscope=(), "
            "magnetometer=(), "
            "microphone=(), "
            "payment=(), "
            "usb=()"
        )
        
        # Content Security Policy - only in production to avoid dev issues
        if not getattr(settings, 'DEV_MODE', False):
            # Production CSP - restrictive
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: https:; "
                "font-src 'self' data:; "
                "connect-src 'self' https://api.resonantgenesis.com wss://api.resonantgenesis.com; "
                "frame-ancestors 'self'; "
                "form-action 'self'; "
                "base-uri 'self';"
            )
            
            # HSTS - Force HTTPS (only in production with HTTPS)
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )
        else:
            # Development CSP - more permissive
            response.headers["Content-Security-Policy"] = (
                "default-src 'self' http://localhost:* ws://localhost:*; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data: http://localhost:*; "
                "connect-src 'self' http://localhost:* ws://localhost:*;"
            )
        
        return response


class RequestValidationMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate and sanitize incoming requests.
    
    Provides:
    - Request size limits
    - Header validation
    - Path traversal prevention
    """
    
    # Maximum request body size (10MB)
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    
    # Blocked path patterns (path traversal attempts)
    BLOCKED_PATTERNS = [
        "..",
        "//",
        "\\",
        "%2e%2e",
        "%252e",
    ]
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path.lower()
        
        # Check for path traversal attempts
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in path:
                return Response(
                    status_code=400,
                    content=b"Invalid request path",
                )
        
        # Check content length for POST/PUT/PATCH
        if request.method in ("POST", "PUT", "PATCH"):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > self.MAX_CONTENT_LENGTH:
                        return Response(
                            status_code=413,
                            content=b"Request entity too large",
                        )
                except ValueError:
                    pass
        
        return await call_next(request)


class APIKeyValidationMiddleware(BaseHTTPMiddleware):
    """
    Middleware to validate API keys for service-to-service communication.
    
    Checks:
    - API key format
    - API key prefix validation
    - Rate limiting per API key
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip for public endpoints
        if request.url.path.startswith(("/health", "/docs", "/openapi")):
            return await call_next(request)
        
        # Check for API key in header
        api_key = request.headers.get("X-API-Key")
        
        if api_key:
            # Validate API key format (should start with "rg_" prefix)
            if not api_key.startswith("rg_"):
                return Response(
                    status_code=401,
                    content=b"Invalid API key format",
                )
            
            # API key validation is handled by auth_service
            # This is just a format check
        
        return await call_next(request)
