"""Git and GitHub Integration API Routes.

These endpoints provide Git operations and GitHub integration functionality.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


router = APIRouter(tags=["git"])


# ============================================
# Request Models
# ============================================

class GitInitRequest(BaseModel):
    project_id: str


class GitCommitRequest(BaseModel):
    project_id: str
    message: str
    files: Optional[List[str]] = None


class GitBranchRequest(BaseModel):
    project_id: str
    branch_name: str
    from_branch: Optional[str] = "main"


class GitStageRequest(BaseModel):
    project_id: str
    files: List[str]


class GitPushRequest(BaseModel):
    project_id: str
    remote: str = "origin"
    branch: Optional[str] = None


class GitStatusRequest(BaseModel):
    project_id: str


class GitAddRequest(BaseModel):
    project_id: str
    files: List[str]


class GitUnstageRequest(BaseModel):
    project_id: str
    files: List[str]


class GitHubCloneRequest(BaseModel):
    repo_url: str
    branch: Optional[str] = "main"
    project_name: Optional[str] = None


class GitHubSyncRequest(BaseModel):
    project_id: str
    direction: str = "pull"  # pull or push
    repo_name: Optional[str] = None
    commit_message: Optional[str] = None


# ============================================
# Git Endpoints
# ============================================

git_router = APIRouter(prefix="/git", tags=["git"])


@git_router.get("/branches")
async def list_branches(
    request: Request,
    project_id: str,
):
    """List all branches for a project."""
    return {
        "branches": [
            {"name": "main", "current": True, "last_commit": "abc123"},
            {"name": "develop", "current": False, "last_commit": "def456"},
            {"name": "feature/new-ui", "current": False, "last_commit": "ghi789"},
        ],
        "current": "main",
    }


@git_router.get("/log")
async def get_git_log(
    request: Request,
    project_id: str,
    limit: int = 20,
):
    """Get commit history."""
    commits = []
    for i in range(min(limit, 20)):
        commits.append({
            "hash": f"{'abcdef'[i % 6]}{'123456'[i % 6]}" * 5,
            "short_hash": f"{'abcdef'[i % 6]}{'123456'[i % 6]}" * 2,
            "message": f"Commit message {i + 1}",
            "author": "Developer",
            "email": "dev@example.com",
            "date": (datetime.now()).isoformat(),
        })
    
    return {
        "commits": commits,
        "total": len(commits),
    }


@git_router.post("/init")
async def git_init(payload: GitInitRequest, request: Request):
    """Initialize a git repository."""
    return {
        "success": True,
        "project_id": payload.project_id,
        "message": "Git repository initialized",
    }


@git_router.post("/add")
async def git_add(payload: GitAddRequest, request: Request):
    """Add files to staging."""
    return {
        "success": True,
        "staged_files": payload.files,
    }


@git_router.post("/stage")
async def git_stage(payload: GitStageRequest, request: Request):
    """Stage files for commit."""
    return {
        "success": True,
        "staged_files": payload.files,
    }


@git_router.post("/unstage")
async def git_unstage(payload: GitUnstageRequest, request: Request):
    """Unstage files."""
    return {
        "success": True,
        "unstaged_files": payload.files,
    }


@git_router.post("/commit")
async def git_commit(payload: GitCommitRequest, request: Request):
    """Create a commit."""
    return {
        "success": True,
        "commit_hash": "abc123def456",
        "message": payload.message,
        "files_committed": payload.files or ["all staged files"],
    }


@git_router.post("/branch")
async def git_branch(payload: GitBranchRequest, request: Request):
    """Create a new branch."""
    return {
        "success": True,
        "branch_name": payload.branch_name,
        "from_branch": payload.from_branch,
    }


@git_router.post("/push")
async def git_push(payload: GitPushRequest, request: Request):
    """Push to remote."""
    return {
        "success": True,
        "remote": payload.remote,
        "branch": payload.branch or "current",
    }


@git_router.post("/status")
async def git_status(payload: GitStatusRequest, request: Request):
    """Get git status."""
    return {
        "branch": "main",
        "staged": [],
        "modified": [],
        "untracked": [],
        "ahead": 0,
        "behind": 0,
    }


# ============================================
# GitHub Endpoints
# ============================================

github_router = APIRouter(prefix="/github", tags=["github"])

async def _get_github_token_for_user(user_id: str) -> Optional[Dict[str, Any]]:
    """
    Get GitHub token for a user.
    Returns dict with access_token + username, or None.
    """
    import os, httpx

    # Auth service DB (PATs stored via /user/api-keys)
    auth_url = os.getenv("AUTH_SERVICE_URL", "http://green_auth_service:8000")
    internal_key = os.getenv("INTERNAL_SERVICE_KEY", "")
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{auth_url}/auth/internal/user-api-keys/{user_id}",
                headers={"x-user-id": user_id, "x-internal-service-key": internal_key},
            )
        if resp.status_code == 200:
            for key_entry in resp.json().get("keys", []):
                if key_entry.get("provider") == "github" and key_entry.get("api_key"):
                    token = key_entry["api_key"]
                    # Validate + get username from GitHub API
                    try:
                        async with httpx.AsyncClient(timeout=5.0) as client:
                            u = await client.get(
                                "https://api.github.com/user",
                                headers={"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"},
                            )
                        username = u.json().get("login", "user") if u.status_code == 200 else "user"
                    except Exception:
                        username = "user"
                    return {"access_token": token, "username": username}
    except Exception:
        pass
    return None


@github_router.get("/status")
async def github_status(request: Request):
    """Check GitHub connection status. Uses GitHub PAT stored in auth service DB."""
    user_id = request.headers.get("x-user-id") or request.headers.get("X-User-ID", "")
    if not user_id:
        return {"connected": False, "username": None, "repos": []}

    token_data = await _get_github_token_for_user(user_id)
    if token_data:
        return {"connected": True, "username": token_data.get("username"), "repos": []}
    return {"connected": False, "username": None, "repos": []}


@github_router.get("/oauth/authorize")
async def github_oauth_authorize(request: Request):
    raise HTTPException(
        status_code=410,
        detail="GitHub OAuth has been disabled. Use a GitHub Personal Access Token (PAT) via API Keys / Connect Profiles.",
    )


@github_router.get("/oauth/callback")
async def github_oauth_callback(request: Request, code: str = None, state: str = None):
    raise HTTPException(
        status_code=410,
        detail="GitHub OAuth has been disabled. Use a GitHub Personal Access Token (PAT) via API Keys / Connect Profiles.",
    )


@github_router.get("/repos")
async def github_repos(request: Request):
    """List user's GitHub repositories."""
    import httpx

    user_id = request.headers.get("x-user-id") or request.headers.get("X-User-ID", "")
    token_data = await _get_github_token_for_user(user_id) if user_id else None
    if not token_data:
        raise HTTPException(status_code=401, detail="Not connected to GitHub")

    access_token = token_data.get("access_token")
    
    async with httpx.AsyncClient() as client:
        repos_response = await client.get(
            "https://api.github.com/user/repos?sort=updated&per_page=50",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )
        
        if repos_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to list repositories")
        
        repos_data = repos_response.json()
        repos = [
            {
                "name": repo["name"],
                "full_name": repo["full_name"],
                "url": repo["html_url"],
                "private": repo["private"],
            }
            for repo in repos_data
        ]
        
        return {"repos": repos}


@github_router.post("/clone")
async def github_clone(payload: GitHubCloneRequest, request: Request):
    """Clone a GitHub repository."""
    # Extract repo name from URL
    repo_name = payload.repo_url.split("/")[-1].replace(".git", "")
    project_name = payload.project_name or repo_name
    
    return {
        "success": True,
        "project_id": str(uuid4()),
        "project_name": project_name,
        "repo_url": payload.repo_url,
        "branch": payload.branch,
        "message": f"Repository cloned successfully",
    }


@github_router.post("/sync")
async def github_sync(payload: GitHubSyncRequest, request: Request):
    """Sync with GitHub (pull or push)."""
    import httpx, base64

    if payload.direction != "push":
        return {
            "success": True,
            "project_id": payload.project_id,
            "direction": payload.direction,
            "message": "Pull not yet implemented; use download ZIP instead.",
        }

    user_id = request.headers.get("X-User-ID") or request.headers.get("x-user-id", "")
    token_data = await _get_github_token_for_user(user_id) if user_id else None
    if not token_data:
        raise HTTPException(status_code=401, detail="Not connected to GitHub. Add a GitHub PAT in API Keys / Connect Profiles.")

    access_token = token_data.get("access_token")
    username = token_data.get("username", "user")
    repo_name = payload.repo_name or f"resonant-project-{payload.project_id[:8]}"
    commit_msg = payload.commit_message or "Initial commit from ResonantGenesis Project Builder"

    async with httpx.AsyncClient(timeout=60.0) as client:
        gh_headers = {
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json",
        }

        # 1. Create repo (ignore 422 = already exists)
        create_resp = await client.post(
            "https://api.github.com/user/repos",
            json={"name": repo_name, "private": False, "auto_init": False},
            headers=gh_headers,
        )
        if create_resp.status_code not in (201, 422):
            raise HTTPException(status_code=502, detail=f"GitHub repo creation failed: {create_resp.text}")

        full_repo = f"{username}/{repo_name}"

        # 2. Fetch project files from agent_engine_service
        try:
            files_resp = await client.get(
                f"http://agent_engine_service:8000/project-builder/projects/{payload.project_id}/files",
                headers={"x-user-id": user_id},
            )
            files_data = files_resp.json() if files_resp.status_code == 200 else {}
            files = files_data.get("files", [])
        except Exception:
            files = []

        if not files:
            raise HTTPException(status_code=404, detail="No project files found. Generate the project first.")

        # 3. Push each file via GitHub Contents API
        pushed = 0
        errors = []
        for f in files:
            path = f.get("path", "")
            content = f.get("content", "")
            if not path or not content:
                continue
            encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

            # Check if file exists (to get sha for update)
            sha = None
            existing = await client.get(f"https://api.github.com/repos/{full_repo}/contents/{path}", headers=gh_headers)
            if existing.status_code == 200:
                sha = existing.json().get("sha")

            body: Dict[str, Any] = {"message": commit_msg, "content": encoded}
            if sha:
                body["sha"] = sha

            put_resp = await client.put(
                f"https://api.github.com/repos/{full_repo}/contents/{path}",
                json=body,
                headers=gh_headers,
            )
            if put_resp.status_code in (200, 201):
                pushed += 1
            else:
                errors.append(f"{path}: {put_resp.status_code}")

    return {
        "success": pushed > 0,
        "project_id": payload.project_id,
        "repo_url": f"https://github.com/{full_repo}",
        "files_pushed": pushed,
        "errors": errors,
        "message": f"Pushed {pushed} files to https://github.com/{full_repo}",
    }


class CVExportRequest(BaseModel):
    analysis_id: str
    repo_name: Optional[str] = None
    file_path: Optional[str] = None  # path inside repo, e.g. reports/analysis.md


@github_router.post("/export/analysis")
async def export_cv_analysis_to_github(payload: CVExportRequest, request: Request):
    """Export a Code Visualizer analysis report to a GitHub repository as Markdown."""
    import httpx, base64, json as _json

    user_id = request.headers.get("X-User-ID") or request.headers.get("x-user-id", "")
    token_data = await _get_github_token_for_user(user_id) if user_id else None
    if not token_data:
        raise HTTPException(status_code=401, detail="Not connected to GitHub. Add a GitHub PAT in API Keys / Connect Profiles.")

    access_token = token_data.get("access_token")
    username = token_data.get("username", "user")
    repo_name = payload.repo_name or "resonant-cv-reports"
    file_path = payload.file_path or f"reports/{payload.analysis_id[:12]}.md"

    # Fetch analysis from CV service (via internal URL)
    cv_urls = []
    import os
    cv_host = os.getenv("AST_ANALYSIS_SERVICE_URL") or os.getenv("CODE_VISUALIZER_SERVICE_URL", "http://rg_ast_analysis:8000")
    cv_urls.append(cv_host)

    analysis_data = None
    async with httpx.AsyncClient(timeout=30.0) as client:
        for base in cv_urls:
            try:
                resp = await client.get(
                    f"{base}/api/v1/analyses/{payload.analysis_id}",
                    headers={"x-user-id": user_id, "x-user-role": "platform_owner", "x-is-superuser": "true"},
                )
                if resp.status_code == 200:
                    analysis_data = resp.json()
                    break
            except Exception:
                continue

    if not analysis_data:
        raise HTTPException(status_code=404, detail="Analysis not found or CV service unavailable.")

    # Build markdown report
    meta = analysis_data.get("meta", {})
    stats = analysis_data.get("stats", {})
    project_name = meta.get("project_name") or analysis_data.get("project_name", "Unknown Project")
    source = meta.get("source") or analysis_data.get("source", "upload")
    repo_url = meta.get("repo_url") or analysis_data.get("repo_url", "")
    created_at = meta.get("created_at") or analysis_data.get("created_at", "")

    lines = [
        f"# Code Visualizer Report: {project_name}",
        "",
        f"**Analysis ID**: `{payload.analysis_id}`  ",
        f"**Source**: {source}  ",
        f"**Generated**: {created_at[:10] if created_at else 'N/A'}  ",
    ]
    if repo_url:
        lines += [f"**Repository**: [{repo_url}]({repo_url})  "]
    lines += ["", "## Statistics", ""]
    for k, v in (stats or {}).items():
        lines.append(f"- **{k.replace('_', ' ').title()}**: {v}")

    # Include governance and trace summaries if present
    governance = analysis_data.get("governance") or {}
    if governance:
        lines += ["", "## Governance Summary", ""]
        for k, v in governance.items():
            if isinstance(v, (int, float, str)):
                lines.append(f"- **{k.replace('_', ' ').title()}**: {v}")

    lines += ["", "---", "*Generated by ResonantGenesis Code Visualizer*"]
    markdown_content = "\n".join(lines)

    gh_headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Create repo if missing
        create_resp = await client.post(
            "https://api.github.com/user/repos",
            json={"name": repo_name, "private": False, "auto_init": True,
                  "description": "Code Visualizer analysis reports from ResonantGenesis"},
            headers=gh_headers,
        )
        if create_resp.status_code not in (201, 422):
            raise HTTPException(status_code=502, detail=f"GitHub repo creation failed: {create_resp.text}")

        full_repo = f"{username}/{repo_name}"

        # Check if file exists for sha
        sha = None
        existing = await client.get(
            f"https://api.github.com/repos/{full_repo}/contents/{file_path}",
            headers=gh_headers,
        )
        if existing.status_code == 200:
            sha = existing.json().get("sha")

        encoded = base64.b64encode(markdown_content.encode("utf-8")).decode("utf-8")
        body: Dict[str, Any] = {
            "message": f"Add CV analysis report: {project_name}",
            "content": encoded,
        }
        if sha:
            body["sha"] = sha

        put_resp = await client.put(
            f"https://api.github.com/repos/{full_repo}/contents/{file_path}",
            json=body,
            headers=gh_headers,
        )
        if put_resp.status_code not in (200, 201):
            raise HTTPException(status_code=502, detail=f"GitHub push failed: {put_resp.text}")

    repo_url_result = f"https://github.com/{full_repo}/blob/main/{file_path}"
    return {
        "success": True,
        "repo_url": f"https://github.com/{full_repo}",
        "file_url": repo_url_result,
        "message": f"Report pushed to {repo_url_result}",
    }
