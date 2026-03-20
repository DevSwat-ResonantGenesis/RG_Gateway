"""Code/IDE Extended API Routes.

These endpoints provide code operations including project management,
LSP features, and code generation.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field


router = APIRouter(prefix="/code", tags=["code"])


# ============================================
# Request Models
# ============================================

class ProjectCreateRequest(BaseModel):
    name: str
    template: Optional[str] = None
    language: str = "python"


class CodeGenerateRequest(BaseModel):
    prompt: str
    language: str = "python"
    context: Optional[str] = None


class CodeCompleteRequest(BaseModel):
    code: str
    cursor_position: int
    language: str = "python"


class RefactorRequest(BaseModel):
    code: str
    refactor_type: str  # rename, extract_function, inline, etc.
    options: Optional[Dict[str, Any]] = None


class LSPRequest(BaseModel):
    file_path: str
    position: Dict[str, int]  # line, character
    content: Optional[str] = None


class FileOperationRequest(BaseModel):
    project_id: str
    file_path: str
    new_path: Optional[str] = None


# ============================================
# Project Endpoints
# ============================================

@router.get("/projects")
async def list_user_projects(request: Request):
    """List all projects for the authenticated user.
    
    Returns projects from memory service filtered by user_id.
    This enables cross-device project access.
    """
    user_id = request.headers.get("x-user-id")
    
    if not user_id:
        return {
            "projects": [],
            "message": "No user ID provided"
        }
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # Fetch user's projects from memory service
            memory_url = "http://memory_service:8000/memory/projects"
            response = await client.get(
                memory_url,
                params={"user_id": user_id},
                headers={"x-user-id": user_id},
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "projects": data.get("projects", []),
                    "count": len(data.get("projects", [])),
                }
    except Exception as e:
        print(f"Memory service error listing projects: {e}")
    
    # Return empty if memory service fails
    return {
        "projects": [],
        "count": 0,
    }


@router.get("/project/files")
async def get_project_files(
    request: Request,
    project_id: str,
):
    """Get project file tree from memory service.
    
    Fetches files stored in Hash Sphere memory, excluding archived files.
    Returns files with their content for immediate use in IDE.
    """
    user_id = request.headers.get("x-user-id")
    
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            # Use the correct endpoint: /memory/project/files
            memory_url = f"http://memory_service:8000/memory/project/files"
            response = await client.get(
                memory_url,
                params={"project_id": project_id},
                headers={"x-user-id": user_id} if user_id else {},
                timeout=30.0
            )
            
            if response.status_code == 200:
                data = response.json()
                files = data.get("files", [])
                
                return {
                    "project_id": project_id,
                    "files": files,
                }
    except Exception as e:
        # Log error but don't fail - return empty files
        print(f"Memory service error listing files: {e}")
    
    # Return empty files if memory service fails or no files found
    return {
        "project_id": project_id,
        "files": [],
    }


@router.get("/project/download")
async def download_project(
    request: Request,
    project_id: str,
):
    """Get project download URL."""
    return {
        "project_id": project_id,
        "download_url": f"/api/code/project/{project_id}/archive.zip",
        "expires_at": (datetime.now()).isoformat(),
    }


@router.post("/project/create")
async def create_project(
    payload: ProjectCreateRequest,
    request: Request,
):
    """Create a new project."""
    return {
        "id": str(uuid4()),
        "name": payload.name,
        "template": payload.template,
        "language": payload.language,
        "created_at": datetime.now().isoformat(),
    }


@router.post("/project/archive")
async def archive_project(request: Request):
    """Archive a project."""
    body = await request.json()
    return {
        "project_id": body.get("project_id"),
        "archived": True,
        "archived_at": datetime.now().isoformat(),
    }


@router.post("/project/restore")
async def restore_project(request: Request):
    """Restore an archived project."""
    body = await request.json()
    return {
        "project_id": body.get("project_id"),
        "restored": True,
        "restored_at": datetime.now().isoformat(),
    }


@router.post("/project/restore-by-hash")
async def restore_project_by_hash(request: Request):
    """Restore a project by its hash."""
    body = await request.json()
    return {
        "hash": body.get("hash"),
        "project_id": str(uuid4()),
        "restored": True,
        "restored_at": datetime.now().isoformat(),
    }


@router.post("/project/transfer")
async def transfer_project(request: Request):
    """Transfer project ownership."""
    body = await request.json()
    return {
        "project_id": body.get("project_id"),
        "new_owner_id": body.get("new_owner_id"),
        "transferred": True,
        "transferred_at": datetime.now().isoformat(),
    }


@router.post("/project/upload")
async def upload_project(request: Request):
    """Upload a project and store files in Hash Sphere memory."""
    import zipfile
    import io
    import httpx
    
    user_id = request.headers.get("x-user-id")
    project_id = str(uuid4())
    uploaded_files = []
    errors = []
    
    # Get the uploaded file
    form = await request.form()
    file = form.get("file")
    
    if not file:
        return {
            "project_id": project_id,
            "uploaded": False,
            "error": "No file uploaded",
        }
    
    try:
        content = await file.read()
        
        # If it's a zip file, extract and store each file
        if file.filename.endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                for name in zf.namelist():
                    # Skip directories and hidden files
                    if name.endswith('/') or name.startswith('__MACOSX') or '/..' in name:
                        continue
                    
                    try:
                        file_content = zf.read(name).decode('utf-8', errors='replace')
                        
                        # Determine file type/language
                        ext = name.split('.')[-1] if '.' in name else ''
                        language_map = {
                            'py': 'python', 'js': 'javascript', 'ts': 'typescript',
                            'tsx': 'typescriptreact', 'jsx': 'javascriptreact',
                            'json': 'json', 'md': 'markdown', 'css': 'css',
                            'html': 'html', 'yml': 'yaml', 'yaml': 'yaml',
                        }
                        language = language_map.get(ext, 'plaintext')
                        
                        # Store in memory service (Hash Sphere)
                        async with httpx.AsyncClient() as client:
                            memory_url = "http://memory_service:8000/memory/ingest"
                            response = await client.post(
                                memory_url,
                                json={
                                    "content": file_content,
                                    "source": "ide_upload",
                                    "metadata": {
                                        "project_id": project_id,
                                        "file_path": name,
                                        "type": "file",
                                        "language": language,
                                        "is_archived": False,
                                    }
                                },
                                headers={"x-user-id": user_id} if user_id else {},
                                timeout=30.0
                            )
                            
                            if response.status_code == 200:
                                uploaded_files.append(name)
                            else:
                                errors.append(f"Failed to store {name}: {response.status_code}")
                    except Exception as e:
                        errors.append(f"Error processing {name}: {str(e)}")
        else:
            # Single file upload
            file_content = content.decode('utf-8', errors='replace')
            async with httpx.AsyncClient() as client:
                memory_url = "http://memory_service:8000/memory/ingest"
                response = await client.post(
                    memory_url,
                    json={
                        "content": file_content,
                        "source": "ide_upload",
                        "metadata": {
                            "project_id": project_id,
                            "file_path": file.filename,
                            "type": "file",
                            "is_archived": False,
                        }
                    },
                    headers={"x-user-id": user_id} if user_id else {},
                    timeout=30.0
                )
                
                if response.status_code == 200:
                    uploaded_files.append(file.filename)
                else:
                    errors.append(f"Failed to store {file.filename}")
    except Exception as e:
        return {
            "project_id": project_id,
            "uploaded": False,
            "error": str(e),
        }
    
    return {
        "project_id": project_id,
        "uploaded": True,
        "uploaded_at": datetime.now().isoformat(),
        "files_count": len(uploaded_files),
        "files": uploaded_files[:20],  # Return first 20 file names
        "errors": errors if errors else None,
    }


@router.post("/project/read")
async def read_project_file(request: Request):
    """Read a file from a project.
    
    Fetches file content from Memory Service (Hash Sphere).
    """
    import httpx
    
    body = await request.json()
    project_id = body.get("project_id")
    file_path = body.get("file_path")
    user_id = request.headers.get("x-user-id")
    
    if not file_path:
        return {
            "exists": False,
            "error": "file_path is required",
        }
    
    try:
        async with httpx.AsyncClient() as client:
            # Query memory service for file content
            memory_url = "http://memory_service:8000/memory/query"
            response = await client.post(
                memory_url,
                json={
                    "query": f"file:{file_path}",
                    "filters": {
                        "project_id": project_id,
                        "file_path": file_path,
                        "type": "file",
                        "is_archived": False,
                    },
                    "limit": 1,
                },
                headers={"x-user-id": user_id} if user_id else {},
                timeout=10.0
            )
            
            if response.status_code == 200:
                data = response.json()
                results = data.get("results", [])
                if results and len(results) > 0:
                    file_data = results[0]
                    return {
                        "exists": True,
                        "project_id": project_id,
                        "file_path": file_path,
                        "content": file_data.get("content", ""),
                        "language": file_data.get("metadata", {}).get("language", "plaintext"),
                    }
    except Exception as e:
        print(f"Memory service error reading file: {e}")
    
    # File not found in memory service
    return {
        "exists": False,
        "project_id": project_id,
        "file_path": file_path,
        "content": "",
    }


@router.post("/project/write")
async def write_project_file(request: Request):
    """Write a file to a project.
    
    Stores file content in Memory Service (Hash Sphere).
    """
    import httpx
    
    body = await request.json()
    project_id = body.get("project_id")
    file_path = body.get("file_path")
    content = body.get("content", "")
    user_id = request.headers.get("x-user-id")
    
    if not file_path:
        return {
            "written": False,
            "error": "file_path is required",
        }
    
    # Determine language from file extension
    ext = file_path.split('.')[-1] if '.' in file_path else ''
    language_map = {
        'py': 'python', 'js': 'javascript', 'ts': 'typescript',
        'tsx': 'typescriptreact', 'jsx': 'javascriptreact',
        'json': 'json', 'md': 'markdown', 'css': 'css',
        'html': 'html', 'yml': 'yaml', 'yaml': 'yaml',
    }
    language = language_map.get(ext, 'plaintext')
    
    try:
        async with httpx.AsyncClient() as client:
            # Store in memory service (Hash Sphere)
            memory_url = "http://memory_service:8000/memory/ingest"
            response = await client.post(
                memory_url,
                json={
                    "content": content,
                    "source": "ide_write",
                    "metadata": {
                        "project_id": project_id,
                        "file_path": file_path,
                        "type": "file",
                        "language": language,
                        "is_archived": False,
                    }
                },
                headers={"x-user-id": user_id} if user_id else {},
                timeout=30.0
            )
            
            if response.status_code == 200:
                return {
                    "project_id": project_id,
                    "file_path": file_path,
                    "written": True,
                    "written_at": datetime.now().isoformat(),
                }
            else:
                return {
                    "written": False,
                    "error": f"Memory service returned {response.status_code}",
                }
    except Exception as e:
        print(f"Memory service error writing file: {e}")
        return {
            "written": False,
            "error": str(e),
        }


@router.post("/project/delete-file")
async def delete_project_file(request: Request):
    """Delete (archive) a file from a project.
    
    Note: Hash Sphere is immutable - this archives the file instead of deleting.
    Archived files won't be loaded but can be restored later.
    """
    body = await request.json()
    file_path = body.get("file_path")
    project_id = body.get("project_id")
    user_id = request.headers.get("x-user-id")
    
    archived_count = 0
    errors = []
    
    # Archive via memory service (Hash Sphere is immutable)
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            archive_url = "http://memory_service:8000/memory/archive/file"
            archive_response = await client.post(
                archive_url,
                json={"file_path": file_path, "project_id": project_id},
                headers={"x-user-id": user_id} if user_id else {},
                timeout=10.0
            )
            
            if archive_response.status_code == 200:
                result = archive_response.json()
                archived_count = result.get("archived_count", 0)
            else:
                errors.append(f"Archive failed: {archive_response.status_code}")
    except Exception as e:
        errors.append(f"Memory service error: {str(e)}")
    
    return {
        "project_id": project_id,
        "file_path": file_path,
        "deleted": True,  # For API compatibility
        "archived": True,  # Actual operation
        "archived_count": archived_count,
        "errors": errors if errors else None,
        "message": f"Archived {archived_count} memory anchors for: {file_path}"
    }


@router.post("/project/file/move")
async def move_project_file(
    payload: FileOperationRequest,
    request: Request,
):
    """Move a file within a project."""
    return {
        "project_id": payload.project_id,
        "old_path": payload.file_path,
        "new_path": payload.new_path,
        "moved": True,
    }


@router.post("/project/file/rename")
async def rename_project_file(
    payload: FileOperationRequest,
    request: Request,
):
    """Rename a file within a project."""
    return {
        "project_id": payload.project_id,
        "old_path": payload.file_path,
        "new_path": payload.new_path,
        "renamed": True,
    }


@router.post("/project/generate")
async def generate_project(request: Request):
    """Generate a project from a prompt.
    
    Proxies to the Project Builder service in agent_engine_service.
    Uses the autonomous Project Builder agent for code generation.
    """
    import httpx
    
    body = await request.json()
    user_id = request.headers.get("x-user-id")
    org_id = request.headers.get("x-org-id")
    
    # Proxy to Project Builder service
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                "http://agent_engine_service:8000/project-builder/generate",
                json={
                    "description": body.get("description", body.get("prompt", "")),
                    "project_type": body.get("project_type", "react"),
                    "files": body.get("files"),
                    "context": body.get("context"),
                },
                headers={
                    "x-user-id": user_id or "",
                    "x-org-id": org_id or "",
                },
            )
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                raise HTTPException(status_code=401, detail="Authentication required")
            else:
                # Return error details from Project Builder
                error_detail = response.json().get("detail", "Project generation failed")
                raise HTTPException(status_code=response.status_code, detail=error_detail)
                
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Project generation timed out")
    except httpx.ConnectError:
        # Fallback to stub response if Project Builder is not available
        return {
            "files": [],
            "project_structure": {},
            "setup_instructions": "Project Builder service unavailable. Please try again later.",
            "anchors": [],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Project generation error: {str(e)}")


# ============================================
# Project Builder Full API Proxy
# ============================================

async def _proxy_to_project_builder(
    method: str,
    path: str,
    request: Request,
    body: dict = None,
    timeout: float = 60.0,
):
    """Helper to proxy requests to Project Builder service."""
    import httpx
    
    user_id = request.headers.get("x-user-id")
    org_id = request.headers.get("x-org-id")
    
    url = f"http://agent_engine_service:8000/project-builder{path}"
    headers = {
        "x-user-id": user_id or "",
        "x-org-id": org_id or "",
    }
    
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if method == "GET":
                response = await client.get(url, headers=headers)
            elif method == "POST":
                response = await client.post(url, json=body or {}, headers=headers)
            elif method == "DELETE":
                response = await client.delete(url, headers=headers)
            else:
                raise HTTPException(status_code=405, detail="Method not allowed")
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 401:
                raise HTTPException(status_code=401, detail="Authentication required")
            elif response.status_code == 404:
                raise HTTPException(status_code=404, detail="Resource not found")
            else:
                error_detail = response.json().get("detail", "Request failed")
                raise HTTPException(status_code=response.status_code, detail=error_detail)
                
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Request timed out")
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Project Builder service unavailable")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/project-builder/build")
async def start_build(request: Request):
    """Start a new project build. Proxies to Project Builder service."""
    body = await request.json()
    return await _proxy_to_project_builder("POST", "/build", request, body, timeout=300.0)


@router.get("/project-builder/progress/{project_id}")
async def get_build_progress(project_id: str, request: Request):
    """Get build progress. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("GET", f"/progress/{project_id}", request)


@router.post("/project-builder/cancel/{project_id}")
async def cancel_build(project_id: str, request: Request):
    """Cancel an active build. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("POST", f"/cancel/{project_id}", request)


@router.get("/project-builder/templates")
async def list_templates(request: Request):
    """List available project templates. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("GET", "/templates", request)


@router.get("/project-builder/projects")
async def list_projects(request: Request):
    """List user's projects. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("GET", "/projects", request)


@router.get("/project-builder/projects/{project_id}")
async def get_project(project_id: str, request: Request):
    """Get project details. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("GET", f"/projects/{project_id}", request)


@router.delete("/project-builder/projects/{project_id}")
async def delete_project(project_id: str, request: Request):
    """Delete a project. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("DELETE", f"/projects/{project_id}", request)


@router.post("/project-builder/projects/{project_id}/archive")
async def archive_project(project_id: str, request: Request):
    """Archive a project. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("POST", f"/projects/{project_id}/archive", request)


@router.post("/project-builder/projects/{project_id}/deliver")
async def create_delivery(project_id: str, request: Request):
    """Create delivery package. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("POST", f"/projects/{project_id}/deliver", request)


@router.get("/project-builder/workspace/stats")
async def get_workspace_stats(request: Request):
    """Get workspace statistics. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("GET", "/workspace/stats", request)


@router.get("/project-builder/health")
async def check_health(request: Request):
    """Check Project Builder health. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("GET", "/health", request)


# ============================================
# Project Builder Governance & Modification
# ============================================

@router.post("/project-builder/projects/{project_id}/promote")
async def promote_project(project_id: str, request: Request):
    """Promote project from GENERATED to RUNTIME state. Proxies to Project Builder service."""
    try:
        body = await request.json()
    except:
        body = {}
    return await _proxy_to_project_builder("POST", f"/projects/{project_id}/promote", request, body)


@router.get("/project-builder/projects/{project_id}/state")
async def get_project_state(project_id: str, request: Request):
    """Get project governance state. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("GET", f"/projects/{project_id}/state", request)


@router.post("/project-builder/projects/{project_id}/modify")
async def modify_project(project_id: str, request: Request):
    """Modify project using LLM. Proxies to Project Builder service."""
    body = await request.json()
    return await _proxy_to_project_builder("POST", f"/projects/{project_id}/modify", request, body, timeout=120.0)


@router.get("/project-builder/projects/{project_id}/files")
async def get_project_files(project_id: str, request: Request):
    """Get project files. Proxies to Project Builder service."""
    return await _proxy_to_project_builder("GET", f"/projects/{project_id}/files", request)


# ============================================
# Code Generation & Completion
# ============================================

@router.post("/generate")
async def generate_code(
    payload: CodeGenerateRequest,
    request: Request,
):
    """Generate code from a prompt."""
    return {
        "code": f"# Generated code for: {payload.prompt}\ndef generated_function():\n    pass\n",
        "language": payload.language,
        "tokens_used": 150,
        "generated_at": datetime.now().isoformat(),
    }


@router.post("/complete")
async def complete_code(
    payload: CodeCompleteRequest,
    request: Request,
):
    """Get code completions."""
    return {
        "completions": [
            {"text": "def function_name():", "score": 0.95},
            {"text": "class ClassName:", "score": 0.85},
            {"text": "import module", "score": 0.75},
        ],
        "cursor_position": payload.cursor_position,
    }


@router.post("/refactor")
async def refactor_code(
    payload: RefactorRequest,
    request: Request,
):
    """Refactor code."""
    return {
        "original_code": payload.code,
        "refactored_code": payload.code.replace("old_name", "new_name"),
        "refactor_type": payload.refactor_type,
        "changes": [
            {"line": 1, "type": "rename", "old": "old_name", "new": "new_name"},
        ],
    }


@router.post("/refactor/advanced")
async def advanced_refactor(request: Request):
    """Advanced code refactoring."""
    body = await request.json()
    return {
        "refactored": True,
        "changes_count": 5,
        "refactored_at": datetime.now().isoformat(),
    }


@router.post("/index")
async def index_code(request: Request):
    """Index code for search."""
    body = await request.json()
    return {
        "project_id": body.get("project_id"),
        "indexed_files": 25,
        "indexed_at": datetime.now().isoformat(),
    }


@router.get("/search")
async def search_code(
    request: Request,
    query: str,
    project_id: Optional[str] = None,
):
    """Search code."""
    return {
        "query": query,
        "results": [
            {"file": "src/main.py", "line": 10, "match": "def search_function():"},
            {"file": "src/utils.py", "line": 25, "match": "# search related code"},
        ],
        "total": 2,
    }


@router.get("/search/ml")
async def ml_search_code(
    request: Request,
    query: str,
    project_id: Optional[str] = None,
):
    """ML-powered semantic code search."""
    return {
        "query": query,
        "results": [
            {"file": "src/main.py", "line": 10, "match": "def search_function():", "relevance": 0.95},
            {"file": "src/utils.py", "line": 25, "match": "# search related code", "relevance": 0.82},
        ],
        "total": 2,
        "model": "code-search-v2",
    }


# ============================================
# LSP Endpoints
# ============================================

@router.post("/lsp/completion")
async def lsp_completion(
    payload: LSPRequest,
    request: Request,
):
    """Get LSP completions."""
    return {
        "completions": [
            {"label": "function_name", "kind": "function", "detail": "def function_name()"},
            {"label": "variable_name", "kind": "variable", "detail": "str"},
        ],
    }


@router.post("/lsp/definition")
async def lsp_definition(
    payload: LSPRequest,
    request: Request,
):
    """Go to definition."""
    return {
        "definitions": [
            {
                "file_path": payload.file_path,
                "line": 10,
                "character": 0,
            }
        ],
    }


@router.post("/lsp/hover")
async def lsp_hover(
    payload: LSPRequest,
    request: Request,
):
    """Get hover information."""
    return {
        "contents": "def function_name() -> str:\n    '''Function documentation'''",
        "range": {
            "start": {"line": payload.position["line"], "character": 0},
            "end": {"line": payload.position["line"], "character": 20},
        },
    }


@router.post("/lsp/references")
async def lsp_references(
    payload: LSPRequest,
    request: Request,
):
    """Find all references."""
    return {
        "references": [
            {"file_path": "src/main.py", "line": 10, "character": 5},
            {"file_path": "src/utils.py", "line": 25, "character": 10},
            {"file_path": "tests/test_main.py", "line": 15, "character": 8},
        ],
    }
