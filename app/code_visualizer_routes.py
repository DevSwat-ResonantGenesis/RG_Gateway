"""AST Analysis (formerly Code Visualizer) UI routes and API proxy.

Routes traffic to the standalone RG AST Analysis microservice.
"""

import os
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
import httpx

router = APIRouter(prefix="/api/v1/code-visualizer-ui", tags=["ast-analysis"])

def _build_ast_analysis_base_urls() -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        u = (url or "").strip().rstrip("/")
        if not u or u in seen:
            return
        seen.add(u)
        urls.append(u)

    # Standalone RG AST Analysis service (preferred)
    add(os.getenv("AST_ANALYSIS_SERVICE_URL") or "")
    # Legacy env vars (fallback)
    add(os.getenv("GATEWAY_CODE_VISUALIZER_URL") or "")
    add(os.getenv("CODE_VISUALIZER_URL") or "")

    # Docker service hostname
    hosts: list[str] = [
        "rg_ast_analysis",
    ]

    for host in hosts:
        add(f"http://{host}:8000")

    return urls


_AST_ANALYSIS_BASE_URLS = _build_ast_analysis_base_urls()


async def _proxy_to_ast_analysis(
    request: Request,
    url_path: str,
    timeout: float,
) -> Response:
    async with httpx.AsyncClient() as client:
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}

        # Ensure identity headers are present for downstream AST Analysis service.
        if "x-user-id" not in {k.lower() for k in headers.keys()} and getattr(request.state, "user_id", None):
            headers["x-user-id"] = str(request.state.user_id)
        if "x-org-id" not in {k.lower() for k in headers.keys()} and getattr(request.state, "org_id", None):
            headers["x-org-id"] = str(request.state.org_id)
        if "x-user-role" not in {k.lower() for k in headers.keys()} and getattr(request.state, "role", None):
            headers["x-user-role"] = str(request.state.role)
        if "x-user-plan" not in {k.lower() for k in headers.keys()} and getattr(request.state, "plan", None):
            headers["x-user-plan"] = str(request.state.plan)
        if "x-is-superuser" not in {k.lower() for k in headers.keys()} and getattr(request.state, "is_superuser", None):
            headers["x-is-superuser"] = str(request.state.is_superuser).lower()
        if "x-unlimited-credits" not in {k.lower() for k in headers.keys()} and getattr(request.state, "unlimited_credits", None):
            headers["x-unlimited-credits"] = str(request.state.unlimited_credits).lower()

        secret = (os.getenv("AST_ANALYSIS_GATEWAY_SECRET") or os.getenv("CODE_VISUALIZER_GATEWAY_SECRET") or "").strip()
        if secret:
            headers["x-ast-analysis-gateway-secret"] = secret
            headers["x-code-visualizer-gateway-secret"] = secret

        body = await request.body()

        last_exc: Exception | None = None
        for base_url in _AST_ANALYSIS_BASE_URLS:
            try:
                resp = await client.request(
                    request.method,
                    f"{base_url}{url_path}",
                    params=request.query_params,
                    content=body if body else None,
                    headers=headers,
                    timeout=timeout,
                )
                return Response(content=resp.content, status_code=resp.status_code, headers=dict(resp.headers))
            except Exception as e:
                last_exc = e
                continue

        raise last_exc or RuntimeError("AST Analysis proxy failed")


@router.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_ast_analysis_api(request: Request, path: str):
    """Proxy /code-visualizer-ui/api/* to RG AST Analysis /api/* (UI-relative API calls)."""
    return await _proxy_to_ast_analysis(request, f"/api/{path}", timeout=60.0)


@router.api_route("/scan/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def proxy_ast_analysis_scan(request: Request, path: str):
    """Proxy /code-visualizer-ui/scan/* to RG AST Analysis /api/v1/scan/*."""
    return await _proxy_to_ast_analysis(request, f"/api/v1/scan/{path}", timeout=900.0)


@router.get("/", response_class=HTMLResponse)
async def ast_analysis_ui(request: Request):
    """Serve AST Analysis UI."""
    try:
        resp = await _proxy_to_ast_analysis(request, "/", timeout=10.0)
        return HTMLResponse(content=resp.body.decode("utf-8", errors="replace"), status_code=resp.status_code)
    except Exception as e:
        return HTMLResponse(
            content=f"<html><body><h1>AST Analysis Service Unavailable</h1><p>{str(e)}</p></body></html>",
            status_code=503,
        )
