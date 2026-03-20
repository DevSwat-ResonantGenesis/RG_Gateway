"""
Knowledge Daemon Service - Agent Chat Backend
Provides chat functionality for admin agent communication using local LLM
"""
import logging
import uuid
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict

from .local_llm import local_llm_service

logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """Represents a single chat message"""
    id: str
    role: str  # 'user', 'assistant', 'system'
    content: str
    timestamp: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ChatSession:
    """Represents a chat session with message history"""
    session_id: str
    user_id: str
    messages: List[ChatMessage] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    system_prompt: str = ""
    model: str = "llama3.1:8b"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "messages": [m.to_dict() for m in self.messages],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "system_prompt": self.system_prompt,
            "model": self.model,
        }


class KnowledgeDaemon:
    """
    Knowledge Daemon - Manages agent chat sessions and LLM interactions
    
    This service provides:
    - Chat session management
    - Message history tracking
    - Local LLM integration via Ollama
    - Admin agent communication backend
    """
    
    # Default system prompt for the knowledge daemon
    DEFAULT_SYSTEM_PROMPT = """You are a helpful AI assistant integrated into the Resonant Genesis platform.
You assist platform administrators and developers with:
- Understanding platform architecture and services
- Debugging issues and analyzing logs
- Providing code suggestions and best practices
- Answering questions about the multi-agent system

Be concise, technical, and helpful. When providing code, use proper formatting."""

    def __init__(self):
        self.sessions: Dict[str, ChatSession] = {}
        self.llm = local_llm_service
        
    def create_session(
        self,
        user_id: str,
        system_prompt: Optional[str] = None,
        model: str = "llama3.1:8b"
    ) -> ChatSession:
        """Create a new chat session"""
        session_id = str(uuid.uuid4())
        session = ChatSession(
            session_id=session_id,
            user_id=user_id,
            system_prompt=system_prompt or self.DEFAULT_SYSTEM_PROMPT,
            model=model,
        )
        self.sessions[session_id] = session
        logger.info(f"Created chat session {session_id} for user {user_id}")
        return session
    
    def get_session(self, session_id: str) -> Optional[ChatSession]:
        """Get an existing chat session"""
        return self.sessions.get(session_id)
    
    def get_user_sessions(self, user_id: str) -> List[ChatSession]:
        """Get all sessions for a user"""
        return [s for s in self.sessions.values() if s.user_id == user_id]
    
    def get_messages(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[ChatMessage]:
        """Get messages from a session with pagination"""
        session = self.get_session(session_id)
        if not session:
            return []
        
        messages = session.messages[offset:offset + limit]
        return messages
    
    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[ChatMessage]:
        """Add a message to a session"""
        session = self.get_session(session_id)
        if not session:
            return None
        
        message = ChatMessage(
            id=str(uuid.uuid4()),
            role=role,
            content=content,
            timestamp=datetime.utcnow().isoformat(),
            metadata=metadata or {},
        )
        session.messages.append(message)
        session.updated_at = datetime.utcnow().isoformat()
        
        return message
    
    async def send_message(
        self,
        session_id: str,
        content: str,
        user_id: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send a message and get LLM response
        
        Args:
            session_id: Chat session ID (creates new if not exists)
            content: User message content
            user_id: User ID for session management
            temperature: LLM temperature
            max_tokens: Max tokens for response
            
        Returns:
            Dict with user message, assistant response, and session info
        """
        # Get or create session
        session = self.get_session(session_id)
        if not session:
            session = self.create_session(user_id)
            session_id = session.session_id
        
        # Add user message
        user_message = self.add_message(session_id, "user", content)
        
        # Build messages for LLM
        llm_messages = []
        
        # Add system prompt
        if session.system_prompt:
            llm_messages.append({
                "role": "system",
                "content": session.system_prompt
            })
        
        # Add conversation history (last 20 messages for context)
        for msg in session.messages[-20:]:
            llm_messages.append({
                "role": msg.role,
                "content": msg.content
            })
        
        try:
            # Get LLM response
            response = await self.llm.chat(
                messages=llm_messages,
                model=session.model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            # Extract assistant response
            assistant_content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            
            # Add assistant message
            assistant_message = self.add_message(session_id, "assistant", assistant_content, {
                "model": session.model,
                "usage": response.get("usage", {}),
            })
            
            return {
                "session_id": session_id,
                "user_message": user_message.to_dict() if user_message else None,
                "assistant_message": assistant_message.to_dict() if assistant_message else None,
                "model": session.model,
                "usage": response.get("usage", {}),
            }
            
        except Exception as e:
            logger.error(f"LLM error in session {session_id}: {str(e)}")
            
            # Add error message
            error_message = self.add_message(
                session_id,
                "assistant",
                f"Error: Unable to get response from LLM. {str(e)}",
                {"error": True}
            )
            
            return {
                "session_id": session_id,
                "user_message": user_message.to_dict() if user_message else None,
                "assistant_message": error_message.to_dict() if error_message else None,
                "error": str(e),
            }
    
    async def health_check(self) -> Dict[str, Any]:
        """Check knowledge daemon and LLM health"""
        llm_healthy = await self.llm.health_check()
        
        return {
            "status": "healthy" if llm_healthy else "degraded",
            "llm_available": llm_healthy,
            "active_sessions": len(self.sessions),
            "total_messages": sum(len(s.messages) for s in self.sessions.values()),
        }
    
    def clear_session(self, session_id: str) -> bool:
        """Clear all messages from a session"""
        session = self.get_session(session_id)
        if session:
            session.messages = []
            session.updated_at = datetime.utcnow().isoformat()
            return True
        return False
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session entirely"""
        if session_id in self.sessions:
            del self.sessions[session_id]
            return True
        return False


# Singleton instance
knowledge_daemon = KnowledgeDaemon()
