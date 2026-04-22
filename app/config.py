import os

from pydantic_settings import BaseSettings


def _svc_url(service: str, port: int) -> str:
    deployment_color = os.getenv("DEPLOYMENT_COLOR", "").strip()
    if deployment_color in {"blue", "green"}:
        host = f"{deployment_color}_{service}_service"
    else:
        host = f"{service}_service"
    return f"http://{host}:{port}"




class Settings(BaseSettings):
    # DEV_MODE: Skip auth for local testing (set GATEWAY_DEV_MODE=true)
    DEV_MODE: bool = False
    
    # Extra hardening: DEV_MODE bypass requires explicit allow.
    # Even when allowed, bypass is still limited to ENVIRONMENT=development.
    ALLOW_DEV_MODE_BYPASS: bool = False
    
    # If true and ALLOW_DEV_MODE_BYPASS is false, only allow bypass when client IP is localhost.
    DEV_MODE_LOCALHOST_ONLY: bool = True
    
    # ENVIRONMENT: Must be 'development' for DEV_MODE to work
    ENVIRONMENT: str = "production"
    
    # JWT Secret Key - MUST match AUTH_JWT_SECRET_KEY for token validation
    # Set via GATEWAY_JWT_SECRET_KEY environment variable
    JWT_SECRET_KEY: str = ""
    
    AUTH_URL: str = _svc_url("auth", 8000)
    USER_URL: str = _svc_url("user", 8000)
    CHAT_URL: str = _svc_url("chat", 8000)
    MEMORY_URL: str = _svc_url("memory", 8000)
    WORKFLOW_URL: str = _svc_url("workflow", 8000)
    STORAGE_URL: str = _svc_url("storage", 8000)
    LLM_URL: str = _svc_url("llm", 8000)
    # ED_URL: str = "http://ed_service:8000"  # DEPRECATED - unused, use ide_platform_service instead
    AGENT_ENGINE_URL: str = _svc_url("agent_engine", 8000)
    BILLING_URL: str = _svc_url("billing", 8000)
    BLOCKCHAIN_URL: str = _svc_url("blockchain", 8000)
    CRYPTO_URL: str = _svc_url("crypto", 8000)
    NOTIFICATION_URL: str = _svc_url("notification", 8000)
    CODE_EXECUTION_URL: str = _svc_url("code_execution", 8000)
    MARKETPLACE_URL: str = _svc_url("marketplace", 8000)
    OPENCLAW_URL: str = _svc_url("openclaw", 8000)

    # Rate limiting
    RATE_LIMIT_PER_MINUTE: int = 10000  # Very high for testing
    RATE_LIMIT_BURST: int = 1000

    # Logging
    LOG_LEVEL: str = "INFO"

    class Config:
        env_prefix = "GATEWAY_"
        case_sensitive = False


settings = Settings()

SERVICE_MAP = {
    "auth": settings.AUTH_URL,
    "user": settings.USER_URL,
    "users": settings.USER_URL,  # alias
    "chat": settings.CHAT_URL,
    "memory": settings.MEMORY_URL,
    "workflow": settings.WORKFLOW_URL,
    "storage": settings.STORAGE_URL,
    "llm": settings.LLM_URL,
    # "ed": settings.ED_URL,  # DEPRECATED - unused, use ide_platform_service instead
    # Core services
    "agents": settings.AGENT_ENGINE_URL,
    "agent-engine": settings.AGENT_ENGINE_URL,
    "agent_engine": settings.AGENT_ENGINE_URL,  # Added underscore version
    "billing": settings.AGENT_ENGINE_URL,  # ORG-level billing (execution metering) in agent_engine_service
    "billing-user": settings.BILLING_URL,  # USER-level billing (credits, invoices, payment) in billing_service
    "blockchain": settings.BLOCKCHAIN_URL,
    "crypto": settings.CRYPTO_URL,
    "notification": settings.NOTIFICATION_URL,
    # Hash Sphere routes to memory service
    "hash-sphere": settings.MEMORY_URL,
    # Code execution microservice
    "code-execution": settings.CODE_EXECUTION_URL,
    # Marketplace service
    "marketplace": settings.MARKETPLACE_URL,
    # OpenClaw federation service (internal only — zero ports exposed)
    "openclaw": settings.OPENCLAW_URL,
}
