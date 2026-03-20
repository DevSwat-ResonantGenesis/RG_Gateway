"""
Local LLM Service - Ollama Integration with Groq Fallback
Provides local LLM inference using Ollama running on localhost,
falls back to Groq API when Ollama is unavailable.
"""
import os
import httpx
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
GROQ_API_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.1-8b-instant"

class LocalLLMService:
    """Service for interacting with local Ollama LLM models with Groq fallback"""
    
    def __init__(self):
        self.base_url = OLLAMA_BASE_URL
        self.timeout = 300.0  # 5 minutes for longer inference
        self._groq_api_key = self._get_groq_key()
        self._ollama_available: Optional[bool] = None
    
    def _get_groq_key(self) -> Optional[str]:
        """Get first Groq API key from env"""
        raw = os.getenv("GROQ_API_KEY", "")
        if raw:
            return raw.split(",")[0].strip()
        return None
    
    async def _groq_chat(self, messages, model, temperature, max_tokens) -> Dict[str, Any]:
        """Fallback: call Groq API in OpenAI-compatible format"""
        if not self._groq_api_key:
            raise Exception("No Groq API key configured and Ollama unavailable")
        groq_model = GROQ_DEFAULT_MODEL
        payload = {
            "model": groq_model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{GROQ_API_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._groq_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            return response.json()
        
    async def generate(
        self,
        prompt: str,
        model: str = "llama3.1:8b",
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False
    ) -> Dict[str, Any]:
        """
        Generate text using local LLM
        
        Args:
            prompt: User prompt
            model: Model name (llama3.1:8b or codellama:13b)
            system: System prompt
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            stream: Whether to stream response
            
        Returns:
            Dict with response text and metadata
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "model": model,
                    "prompt": prompt,
                    "stream": stream,
                    "options": {
                        "temperature": temperature,
                    }
                }
                
                if system:
                    payload["system"] = system
                    
                if max_tokens:
                    payload["options"]["num_predict"] = max_tokens
                
                response = await client.post(
                    f"{self.base_url}/api/generate",
                    json=payload
                )
                response.raise_for_status()
                
                result = response.json()
                
                return {
                    "response": result.get("response", ""),
                    "model": model,
                    "done": result.get("done", False),
                    "context": result.get("context", []),
                    "total_duration": result.get("total_duration", 0),
                    "load_duration": result.get("load_duration", 0),
                    "prompt_eval_count": result.get("prompt_eval_count", 0),
                    "eval_count": result.get("eval_count", 0),
                }
                
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            logger.warning(f"Ollama unavailable for generate, trying Groq fallback: {e}")
            self._ollama_available = False
            msgs = [{"role": "user", "content": prompt}]
            if system:
                msgs.insert(0, {"role": "system", "content": system})
            result = await self._groq_chat(msgs, model, temperature, max_tokens)
            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return {
                "response": content,
                "model": result.get("model", "groq-fallback"),
                "done": True,
                "context": [],
                "total_duration": 0,
                "load_duration": 0,
                "prompt_eval_count": result.get("usage", {}).get("prompt_tokens", 0),
                "eval_count": result.get("usage", {}).get("completion_tokens", 0),
            }
        except Exception as e:
            logger.error(f"Local LLM error: {str(e)}")
            raise
    
    async def chat(
        self,
        messages: List[Dict[str, str]],
        model: str = "llama3.1:8b",
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False
    ) -> Dict[str, Any]:
        """
        Chat completion using local LLM (OpenAI-compatible format)
        
        Args:
            messages: List of message dicts with 'role' and 'content'
            model: Model name
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            stream: Whether to stream response
            
        Returns:
            Dict with response in OpenAI-compatible format
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                payload = {
                    "model": model,
                    "messages": messages,
                    "stream": stream,
                    "options": {
                        "temperature": temperature,
                    }
                }
                
                if max_tokens:
                    payload["options"]["num_predict"] = max_tokens
                
                response = await client.post(
                    f"{self.base_url}/api/chat",
                    json=payload
                )
                response.raise_for_status()
                
                result = response.json()
                
                # Convert to OpenAI-compatible format
                return {
                    "id": "local-llm",
                    "object": "chat.completion",
                    "created": 0,
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": result.get("message", {}).get("content", "")
                        },
                        "finish_reason": "stop" if result.get("done") else "length"
                    }],
                    "usage": {
                        "prompt_tokens": result.get("prompt_eval_count", 0),
                        "completion_tokens": result.get("eval_count", 0),
                        "total_tokens": result.get("prompt_eval_count", 0) + result.get("eval_count", 0)
                    }
                }
                
        except (httpx.ConnectError, httpx.ConnectTimeout) as e:
            logger.warning(f"Ollama unavailable for chat, trying Groq fallback: {e}")
            self._ollama_available = False
            return await self._groq_chat(messages, model, temperature, max_tokens)
        except Exception as e:
            logger.error(f"Local LLM chat error: {str(e)}")
            raise
    
    async def list_models(self) -> List[Dict[str, Any]]:
        """List available local models"""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                response.raise_for_status()
                
                result = response.json()
                return result.get("models", [])
                
        except Exception as e:
            logger.error(f"Error listing models: {str(e)}")
            return []
    
    async def health_check(self) -> bool:
        """Check if Ollama or Groq is available"""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(f"{self.base_url}/api/tags")
                if response.status_code == 200:
                    self._ollama_available = True
                    return True
        except:
            self._ollama_available = False
        # Fallback: check if Groq key is configured
        if self._groq_api_key:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get(
                        f"{GROQ_API_URL}/models",
                        headers={"Authorization": f"Bearer {self._groq_api_key}"},
                    )
                    return response.status_code == 200
            except:
                pass
        return False


# Singleton instance
local_llm_service = LocalLLMService()
