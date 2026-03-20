"""
Agent Chat API Endpoints
Provides admin agent chat functionality via Knowledge Daemon
"""
from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

from app.services.knowledge_daemon import knowledge_daemon

router = APIRouter()


# ============================================
# REQUEST/RESPONSE MODELS
# ============================================

class SendMessageRequest(BaseModel):
    """Request to send a message"""
    content: str = Field(..., description="Message content")
    session_id: Optional[str] = Field(None, description="Session ID (creates new if not provided)")
    temperature: float = Field(0.7, ge=0.0, le=2.0, description="LLM temperature")
    max_tokens: Optional[int] = Field(None, description="Max tokens for response")


class MessageResponse(BaseModel):
    """Single message response"""
    id: str
    role: str
    content: str
    timestamp: str
    metadata: Dict[str, Any] = {}


class SendMessageResponse(BaseModel):
    """Response from sending a message"""
    session_id: str
    user_message: Optional[MessageResponse] = None
    assistant_message: Optional[MessageResponse] = None
    model: Optional[str] = None
    usage: Dict[str, Any] = {}
    error: Optional[str] = None


class MessagesResponse(BaseModel):
    """Response with list of messages"""
    session_id: str
    messages: List[MessageResponse]
    total: int


class SessionResponse(BaseModel):
    """Chat session info"""
    session_id: str
    user_id: str
    created_at: str
    updated_at: str
    message_count: int
    model: str


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    llm_available: bool
    active_sessions: int
    total_messages: int


# ============================================
# ENDPOINTS
# ============================================

@router.get("/messages", response_model=MessagesResponse)
async def get_messages(
    session_id: str = Query(..., description="Chat session ID"),
    limit: int = Query(50, ge=1, le=100, description="Max messages to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    x_user_id: str = Query(None, alias="x-user-id", description="User ID from header"),
):
    """
    Get messages from a chat session
    
    Returns paginated list of messages from the specified session.
    """
    session = knowledge_daemon.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    messages = knowledge_daemon.get_messages(session_id, limit=limit, offset=offset)
    
    return MessagesResponse(
        session_id=session_id,
        messages=[MessageResponse(**m.to_dict()) for m in messages],
        total=len(session.messages),
    )


@router.post("/send", response_model=SendMessageResponse)
async def send_message(
    request: SendMessageRequest,
    x_user_id: str = Query("admin", alias="x-user-id", description="User ID from header"),
):
    """
    Send a message and get LLM response
    
    Creates a new session if session_id is not provided.
    Returns both the user message and assistant response.
    """
    session_id = request.session_id or ""
    
    result = await knowledge_daemon.send_message(
        session_id=session_id,
        content=request.content,
        user_id=x_user_id,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )
    
    return SendMessageResponse(
        session_id=result.get("session_id", ""),
        user_message=MessageResponse(**result["user_message"]) if result.get("user_message") else None,
        assistant_message=MessageResponse(**result["assistant_message"]) if result.get("assistant_message") else None,
        model=result.get("model"),
        usage=result.get("usage", {}),
        error=result.get("error"),
    )


@router.get("/sessions", response_model=List[SessionResponse])
async def get_sessions(
    x_user_id: str = Query("admin", alias="x-user-id", description="User ID from header"),
):
    """
    Get all chat sessions for the current user
    """
    sessions = knowledge_daemon.get_user_sessions(x_user_id)
    
    return [
        SessionResponse(
            session_id=s.session_id,
            user_id=s.user_id,
            created_at=s.created_at,
            updated_at=s.updated_at,
            message_count=len(s.messages),
            model=s.model,
        )
        for s in sessions
    ]


@router.post("/sessions/new", response_model=SessionResponse)
async def create_session(
    system_prompt: Optional[str] = None,
    model: str = "llama3.1:8b",
    x_user_id: str = Query("admin", alias="x-user-id", description="User ID from header"),
):
    """
    Create a new chat session
    """
    session = knowledge_daemon.create_session(
        user_id=x_user_id,
        system_prompt=system_prompt,
        model=model,
    )
    
    return SessionResponse(
        session_id=session.session_id,
        user_id=session.user_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        message_count=0,
        model=session.model,
    )


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str):
    """
    Delete a chat session
    """
    if knowledge_daemon.delete_session(session_id):
        return {"status": "deleted", "session_id": session_id}
    raise HTTPException(status_code=404, detail="Session not found")


@router.post("/sessions/{session_id}/clear")
async def clear_session(session_id: str):
    """
    Clear all messages from a session
    """
    if knowledge_daemon.clear_session(session_id):
        return {"status": "cleared", "session_id": session_id}
    raise HTTPException(status_code=404, detail="Session not found")


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """
    Check knowledge daemon and LLM health
    """
    health = await knowledge_daemon.health_check()
    return HealthResponse(**health)
