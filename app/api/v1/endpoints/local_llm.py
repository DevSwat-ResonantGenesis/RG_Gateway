"""
Local LLM API Endpoints
Provides REST API for local LLM inference
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from app.services.local_llm import local_llm_service
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


class GenerateRequest(BaseModel):
    """Request for text generation"""
    prompt: str = Field(..., description="User prompt")
    model: str = Field(default="llama3.1:8b", description="Model name (llama3.1:8b or codellama:13b)")
    system: Optional[str] = Field(None, description="System prompt")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens to generate")
    stream: bool = Field(default=False, description="Stream response")


class ChatMessage(BaseModel):
    """Chat message"""
    role: str = Field(..., description="Message role (system, user, assistant)")
    content: str = Field(..., description="Message content")


class ChatRequest(BaseModel):
    """Request for chat completion"""
    messages: List[ChatMessage] = Field(..., description="List of chat messages")
    model: str = Field(default="llama3.1:8b", description="Model name")
    temperature: float = Field(default=0.7, ge=0.0, le=2.0, description="Sampling temperature")
    max_tokens: Optional[int] = Field(None, description="Maximum tokens to generate")
    stream: bool = Field(default=False, description="Stream response")


@router.post("/generate")
async def generate_text(
    request: GenerateRequest
):
    """
    Generate text using local LLM
    
    Supports both llama3.1:8b (general) and codellama:13b (coding)
    """
    try:
        result = await local_llm_service.generate(
            prompt=request.prompt,
            model=request.model,
            system=request.system,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=request.stream
        )
        
        return {
            "success": True,
            "data": result
        }
        
    except Exception as e:
        logger.error(f"Generate error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat")
async def chat_completion(
    request: ChatRequest
):
    """
    Chat completion using local LLM (OpenAI-compatible format)
    
    Supports both llama3.1:8b (general) and codellama:13b (coding)
    """
    try:
        messages = [msg.dict() for msg in request.messages]
        
        result = await local_llm_service.chat(
            messages=messages,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=request.stream
        )
        
        return result
        
    except Exception as e:
        logger.error(f"Chat error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models")
async def list_models():
    """
    List available local LLM models
    """
    try:
        models = await local_llm_service.list_models()
        
        return {
            "success": True,
            "models": models,
            "count": len(models)
        }
        
    except Exception as e:
        logger.error(f"List models error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/health")
async def health_check():
    """
    Check if local LLM service is available
    """
    is_healthy = await local_llm_service.health_check()
    
    if not is_healthy:
        raise HTTPException(
            status_code=503,
            detail="Local LLM service unavailable. Please start Ollama."
        )
    
    return {
        "success": True,
        "status": "healthy",
        "service": "ollama",
        "base_url": "http://localhost:11434"
    }


@router.post("/code")
async def generate_code(
    request: GenerateRequest
):
    """
    Generate code using CodeLlama model
    
    Automatically uses codellama:13b for code generation
    """
    try:
        # Force use of CodeLlama for code generation
        result = await local_llm_service.generate(
            prompt=request.prompt,
            model="codellama:13b",
            system=request.system or "You are an expert programmer. Generate clean, efficient, well-documented code.",
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=request.stream
        )
        
        return {
            "success": True,
            "data": result,
            "model": "codellama:13b"
        }
        
    except Exception as e:
        logger.error(f"Code generation error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
