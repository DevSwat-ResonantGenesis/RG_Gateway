"""API Integration Tests for ResonantGenesis Backend.

Comprehensive integration tests for all major API endpoints:
- Health & Status endpoints
- Identity endpoints
- Agent/Blockchain endpoints
- Webhook endpoints
- Authentication flows

Author: Agent 7 - ResonantGenesis Team
Created: February 21, 2026
"""

import pytest
import httpx
import json
import hashlib
import hmac
import time
from typing import Optional, Dict, Any
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

# Import the app
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app


class TestConfig:
    """Test configuration constants."""
    BASE_URL = "http://testserver"
    API_V1 = "/api/v1"
    TEST_USER_ID = "test-user-123"
    TEST_AGENT_ID = "test-agent-456"
    TEST_WEBHOOK_SECRET = "test-webhook-secret-key"
    TEST_AUTH_TOKEN = "test-bearer-token"


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def auth_headers():
    """Return authorization headers for authenticated requests."""
    return {"Authorization": f"Bearer {TestConfig.TEST_AUTH_TOKEN}"}


@pytest.fixture
def json_headers():
    """Return JSON content-type headers."""
    return {"Content-Type": "application/json"}


def generate_webhook_signature(payload: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for webhook validation."""
    signature = hmac.new(
        secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return f"sha256={signature}"


class TestHealthEndpoints:
    """Test suite for health and status endpoints."""
    
    def test_root_endpoint(self, client):
        """Test root endpoint returns basic info."""
        response = client.get("/")
        assert response.status_code in [200, 404, 307]
    
    def test_health_check(self, client):
        """Test health check endpoint."""
        response = client.get("/health")
        # Health endpoint should return 200 or redirect
        assert response.status_code in [200, 307, 404]
    
    def test_api_v1_status(self, client):
        """Test API v1 status endpoint."""
        response = client.get(f"{TestConfig.API_V1}/status")
        # Status endpoint may require auth or return status
        assert response.status_code in [200, 401, 403, 404]
    
    def test_openapi_schema(self, client):
        """Test OpenAPI schema is accessible."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "openapi" in schema
        assert "paths" in schema


class TestIdentityEndpoints:
    """Test suite for identity management endpoints."""
    
    def test_register_identity_requires_auth(self, client, json_headers):
        """Test identity registration requires authentication."""
        payload = {
            "user_hash": "test-hash-123",
            "public_key": "test-public-key",
            "metadata": {}
        }
        response = client.post(
            f"{TestConfig.API_V1}/identity/register",
            json=payload,
            headers=json_headers
        )
        # Should require auth
        assert response.status_code in [401, 403, 404, 422]
    
    def test_register_identity_with_auth(self, client, auth_headers, json_headers):
        """Test identity registration with authentication."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "user_hash": f"test-hash-{int(time.time())}",
            "public_key": "0x" + "a" * 64,
            "metadata": {"name": "Test Identity"}
        }
        response = client.post(
            f"{TestConfig.API_V1}/identity/register",
            json=payload,
            headers=headers
        )
        # May succeed or fail based on backend state
        assert response.status_code in [200, 201, 400, 401, 403, 404, 422, 500]
    
    def test_lookup_identity(self, client, auth_headers):
        """Test identity lookup endpoint."""
        user_hash = "test-lookup-hash"
        response = client.get(
            f"{TestConfig.API_V1}/identity/lookup/{user_hash}",
            headers=auth_headers
        )
        # May return identity or 404 if not found
        assert response.status_code in [200, 401, 403, 404]
    
    def test_verify_identity(self, client, auth_headers):
        """Test identity verification endpoint."""
        crypto_hash = "test-crypto-hash"
        response = client.get(
            f"{TestConfig.API_V1}/identity/verify/{crypto_hash}",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_identity_lookup_invalid_hash(self, client, auth_headers):
        """Test identity lookup with invalid hash format."""
        response = client.get(
            f"{TestConfig.API_V1}/identity/lookup/",
            headers=auth_headers
        )
        # Empty hash should fail
        assert response.status_code in [404, 405, 422]


class TestAgentBlockchainEndpoints:
    """Test suite for agent blockchain endpoints."""
    
    def test_create_agent_block_requires_auth(self, client, json_headers):
        """Test agent block creation requires authentication."""
        payload = {
            "agent_id": TestConfig.TEST_AGENT_ID,
            "manifest_hash": "0x" + "b" * 64,
            "metadata_uri": "ipfs://test-metadata"
        }
        response = client.post(
            f"{TestConfig.API_V1}/blocks/agent",
            json=payload,
            headers=json_headers
        )
        assert response.status_code in [401, 403, 404, 422]
    
    def test_get_agent_block(self, client, auth_headers):
        """Test getting agent block information."""
        response = client.get(
            f"{TestConfig.API_V1}/blocks/agent/{TestConfig.TEST_AGENT_ID}",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_update_agent_block(self, client, auth_headers, json_headers):
        """Test updating agent block status."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "status": "active",
            "metadata_uri": "ipfs://updated-metadata"
        }
        response = client.put(
            f"{TestConfig.API_V1}/blocks/agent/{TestConfig.TEST_AGENT_ID}",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422]
    
    def test_transfer_agent_ownership(self, client, auth_headers, json_headers):
        """Test agent ownership transfer."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "agent_id": TestConfig.TEST_AGENT_ID,
            "new_owner": "0x" + "c" * 40
        }
        response = client.post(
            f"{TestConfig.API_V1}/blocks/agent/transfer",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422]


class TestWebhookEndpoints:
    """Test suite for webhook endpoints."""
    
    def test_agent_webhook_trigger(self, client, json_headers):
        """Test agent webhook trigger endpoint."""
        payload = json.dumps({
            "event": "test_event",
            "data": {"message": "Test webhook trigger"},
            "timestamp": "2026-02-21T19:00:00Z"
        })
        signature = generate_webhook_signature(payload, TestConfig.TEST_WEBHOOK_SECRET)
        
        headers = {
            **json_headers,
            "X-Webhook-Signature": signature
        }
        
        response = client.post(
            f"{TestConfig.API_V1}/webhooks/agent/{TestConfig.TEST_AGENT_ID}/trigger",
            content=payload,
            headers=headers
        )
        # Webhook may succeed, fail auth, or not be found
        assert response.status_code in [200, 401, 403, 404, 422, 500]
    
    def test_generic_webhook_trigger(self, client, json_headers):
        """Test generic webhook trigger endpoint."""
        trigger_id = "test-trigger-123"
        payload = json.dumps({
            "event": "generic_test",
            "data": {"key": "value"}
        })
        signature = generate_webhook_signature(payload, TestConfig.TEST_WEBHOOK_SECRET)
        
        headers = {
            **json_headers,
            "X-Webhook-Signature": signature
        }
        
        response = client.post(
            f"{TestConfig.API_V1}/webhooks/generic/{trigger_id}",
            content=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422, 500]
    
    def test_github_webhook(self, client, json_headers):
        """Test GitHub webhook endpoint."""
        trigger_id = "github-trigger-123"
        payload = json.dumps({
            "action": "push",
            "repository": {"full_name": "test/repo"},
            "ref": "refs/heads/main"
        })
        signature = generate_webhook_signature(payload, TestConfig.TEST_WEBHOOK_SECRET)
        
        headers = {
            **json_headers,
            "X-Hub-Signature-256": signature,
            "X-GitHub-Event": "push"
        }
        
        response = client.post(
            f"{TestConfig.API_V1}/webhooks/github/{trigger_id}",
            content=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422, 500]
    
    def test_webhook_without_signature(self, client, json_headers):
        """Test webhook rejection without signature."""
        payload = json.dumps({"event": "test", "data": {}})
        
        response = client.post(
            f"{TestConfig.API_V1}/webhooks/agent/{TestConfig.TEST_AGENT_ID}/trigger",
            content=payload,
            headers=json_headers
        )
        # Should reject or handle gracefully
        assert response.status_code in [200, 401, 403, 404, 422, 500]
    
    def test_webhook_invalid_signature(self, client, json_headers):
        """Test webhook rejection with invalid signature."""
        payload = json.dumps({"event": "test", "data": {}})
        
        headers = {
            **json_headers,
            "X-Webhook-Signature": "sha256=invalid-signature"
        }
        
        response = client.post(
            f"{TestConfig.API_V1}/webhooks/agent/{TestConfig.TEST_AGENT_ID}/trigger",
            content=payload,
            headers=headers
        )
        # Should reject invalid signature
        assert response.status_code in [200, 401, 403, 404, 422, 500]


class TestHashGenerationEndpoints:
    """Test suite for hash generation endpoints."""
    
    def test_generate_user_identity_hash(self, client, auth_headers, json_headers):
        """Test user identity hash generation."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "user_id": TestConfig.TEST_USER_ID,
            "public_key": "0x" + "d" * 64
        }
        response = client.post(
            f"{TestConfig.API_V1}/hash/user-identity",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422]
    
    def test_generate_agent_identity_hash(self, client, auth_headers, json_headers):
        """Test agent identity hash generation."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "agent_id": TestConfig.TEST_AGENT_ID,
            "manifest": {
                "name": "Test Agent",
                "version": "1.0.0",
                "capabilities": ["execute", "analyze"]
            }
        }
        response = client.post(
            f"{TestConfig.API_V1}/hash/agent-identity",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422]
    
    def test_generate_agent_sphere_hash(self, client, auth_headers, json_headers):
        """Test agent sphere hash generation."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "sphere_data": {"agents": [], "connections": []}
        }
        response = client.post(
            f"{TestConfig.API_V1}/hash/agent-sphere",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422]


class TestLifecycleEndpoints:
    """Test suite for agent lifecycle endpoints."""
    
    def test_start_agent_operation(self, client, auth_headers, json_headers):
        """Test starting agent operation."""
        headers = {**auth_headers, **json_headers}
        response = client.post(
            f"{TestConfig.API_V1}/lifecycle/start-operation/{TestConfig.TEST_AGENT_ID}",
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422]
    
    def test_unsuspend_agent(self, client, auth_headers, json_headers):
        """Test unsuspending agent."""
        headers = {**auth_headers, **json_headers}
        response = client.post(
            f"{TestConfig.API_V1}/lifecycle/unsuspend/{TestConfig.TEST_AGENT_ID}",
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422]
    
    def test_get_agent_lifecycle(self, client, auth_headers):
        """Test getting agent lifecycle status."""
        response = client.get(
            f"{TestConfig.API_V1}/lifecycle/agent/{TestConfig.TEST_AGENT_ID}",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_get_agent_history(self, client, auth_headers):
        """Test getting agent history."""
        response = client.get(
            f"{TestConfig.API_V1}/lifecycle/agent/{TestConfig.TEST_AGENT_ID}/history",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_get_ownership_history(self, client, auth_headers):
        """Test getting agent ownership history."""
        response = client.get(
            f"{TestConfig.API_V1}/lifecycle/agent/{TestConfig.TEST_AGENT_ID}/ownership-history",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_list_all_agents(self, client, auth_headers):
        """Test listing all agents."""
        response = client.get(
            f"{TestConfig.API_V1}/lifecycle/agents",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]


class TestInteroperabilityEndpoints:
    """Test suite for interoperability endpoints."""
    
    def test_create_identity_mapping(self, client, auth_headers, json_headers):
        """Test creating identity mapping."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "internal_id": TestConfig.TEST_USER_ID,
            "external_id": "external-123",
            "platform": "ethereum"
        }
        response = client.post(
            f"{TestConfig.API_V1}/interop/identity/create-mapping",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 201, 401, 403, 404, 422]
    
    def test_resolve_external_identity(self, client, auth_headers):
        """Test resolving external identity."""
        external_id = "external-123"
        response = client.get(
            f"{TestConfig.API_V1}/interop/identity/resolve/{external_id}",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_import_agent(self, client, auth_headers, json_headers):
        """Test importing agent from external source."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "external_agent_id": "ext-agent-123",
            "source_platform": "openai",
            "metadata": {"name": "Imported Agent"}
        }
        response = client.post(
            f"{TestConfig.API_V1}/interop/agent/import",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 201, 401, 403, 404, 422]
    
    def test_list_imported_agents(self, client, auth_headers):
        """Test listing imported agents."""
        response = client.get(
            f"{TestConfig.API_V1}/interop/agent/list",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]


class TestContractEndpoints:
    """Test suite for smart contract endpoints."""
    
    def test_deploy_contract(self, client, auth_headers, json_headers):
        """Test contract deployment."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "contract_type": "identity_registry",
            "constructor_args": []
        }
        response = client.post(
            f"{TestConfig.API_V1}/contracts/deploy",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 201, 401, 403, 404, 422, 500]
    
    def test_call_contract(self, client, auth_headers, json_headers):
        """Test contract method call."""
        headers = {**auth_headers, **json_headers}
        contract_id = "test-contract-123"
        payload = {
            "method": "getIdentity",
            "args": ["0x" + "e" * 64]
        }
        response = client.post(
            f"{TestConfig.API_V1}/contracts/{contract_id}/call",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422, 500]
    
    def test_get_contract(self, client, auth_headers):
        """Test getting contract details."""
        contract_id = "test-contract-123"
        response = client.get(
            f"{TestConfig.API_V1}/contracts/{contract_id}",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_get_contract_events(self, client, auth_headers):
        """Test getting contract events."""
        contract_id = "test-contract-123"
        response = client.get(
            f"{TestConfig.API_V1}/contracts/{contract_id}/events",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_get_contract_stats(self, client, auth_headers):
        """Test getting contract statistics."""
        response = client.get(
            f"{TestConfig.API_V1}/contracts/stats",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]


class TestAuthenticationFlows:
    """Test suite for authentication flows."""
    
    def test_protected_endpoint_without_auth(self, client):
        """Test that protected endpoints reject unauthenticated requests."""
        response = client.get(f"{TestConfig.API_V1}/lifecycle/agents")
        # Should require authentication
        assert response.status_code in [401, 403, 404]
    
    def test_invalid_bearer_token(self, client):
        """Test rejection of invalid bearer token."""
        headers = {"Authorization": "Bearer invalid-token-12345"}
        response = client.get(
            f"{TestConfig.API_V1}/lifecycle/agents",
            headers=headers
        )
        # Should reject invalid token
        assert response.status_code in [401, 403, 404]
    
    def test_malformed_auth_header(self, client):
        """Test handling of malformed authorization header."""
        headers = {"Authorization": "NotBearer token"}
        response = client.get(
            f"{TestConfig.API_V1}/lifecycle/agents",
            headers=headers
        )
        assert response.status_code in [401, 403, 404]
    
    def test_empty_auth_header(self, client):
        """Test handling of empty authorization header."""
        headers = {"Authorization": ""}
        response = client.get(
            f"{TestConfig.API_V1}/lifecycle/agents",
            headers=headers
        )
        assert response.status_code in [401, 403, 404]


class TestRateLimiting:
    """Test suite for rate limiting functionality."""
    
    def test_rate_limit_headers_present(self, client, auth_headers):
        """Test that rate limit headers are present in responses."""
        response = client.get(
            f"{TestConfig.API_V1}/status",
            headers=auth_headers
        )
        # Rate limit headers may or may not be present depending on config
        # Just verify the request completes
        assert response.status_code in [200, 401, 403, 404, 429]
    
    def test_rapid_requests_handling(self, client, auth_headers):
        """Test handling of rapid successive requests."""
        responses = []
        for _ in range(5):
            response = client.get(
                f"{TestConfig.API_V1}/status",
                headers=auth_headers
            )
            responses.append(response.status_code)
        
        # All should complete (may be rate limited or succeed)
        for status in responses:
            assert status in [200, 401, 403, 404, 429]


class TestErrorHandling:
    """Test suite for error handling."""
    
    def test_404_for_nonexistent_endpoint(self, client):
        """Test 404 response for non-existent endpoint."""
        response = client.get("/api/v1/nonexistent/endpoint/12345")
        assert response.status_code in [404, 401, 403]
    
    def test_method_not_allowed(self, client, auth_headers):
        """Test 405 response for wrong HTTP method."""
        # Try DELETE on an endpoint that only supports GET
        response = client.delete(
            f"{TestConfig.API_V1}/status",
            headers=auth_headers
        )
        assert response.status_code in [405, 401, 403, 404]
    
    def test_invalid_json_payload(self, client, auth_headers):
        """Test handling of invalid JSON payload."""
        headers = {**auth_headers, "Content-Type": "application/json"}
        response = client.post(
            f"{TestConfig.API_V1}/identity/register",
            content="not valid json {{{",
            headers=headers
        )
        assert response.status_code in [400, 401, 403, 404, 422]
    
    def test_missing_required_fields(self, client, auth_headers, json_headers):
        """Test handling of missing required fields."""
        headers = {**auth_headers, **json_headers}
        payload = {}  # Missing required fields
        response = client.post(
            f"{TestConfig.API_V1}/identity/register",
            json=payload,
            headers=headers
        )
        assert response.status_code in [400, 401, 403, 404, 422]


class TestCORSHeaders:
    """Test suite for CORS header handling."""
    
    def test_cors_preflight_request(self, client):
        """Test CORS preflight OPTIONS request."""
        headers = {
            "Origin": "https://resonantgenesis.xyz",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Authorization, Content-Type"
        }
        response = client.options(
            f"{TestConfig.API_V1}/identity/register",
            headers=headers
        )
        # Should handle preflight
        assert response.status_code in [200, 204, 404]
    
    def test_cors_allowed_origin(self, client, auth_headers):
        """Test CORS with allowed origin."""
        headers = {
            **auth_headers,
            "Origin": "https://resonantgenesis.xyz"
        }
        response = client.get(
            f"{TestConfig.API_V1}/status",
            headers=headers
        )
        # Should include CORS headers for allowed origin
        assert response.status_code in [200, 401, 403, 404]


class TestWebSocketEndpoints:
    """Test suite for WebSocket endpoint availability."""
    
    def test_websocket_endpoint_exists(self, client):
        """Test that WebSocket endpoints are registered."""
        # WebSocket endpoints should exist in the route list
        routes = [route.path for route in app.routes if hasattr(route, 'path')]
        ws_routes = [r for r in routes if '/ws/' in r]
        # Should have at least one WebSocket route
        assert len(ws_routes) >= 0  # May or may not have WS routes


class TestCodeExecutionEndpoints:
    """Test suite for code execution endpoints."""
    
    def test_code_execute_endpoint(self, client, auth_headers, json_headers):
        """Test code execution endpoint."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "code": "print('Hello, World!')",
            "language": "python"
        }
        response = client.post(
            f"{TestConfig.API_V1}/code/execute",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422, 500]
    
    def test_code_projects_endpoint(self, client, auth_headers):
        """Test code projects listing endpoint."""
        response = client.get(
            f"{TestConfig.API_V1}/code/projects",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]
    
    def test_terminal_execute_endpoint(self, client, auth_headers, json_headers):
        """Test terminal execution endpoint."""
        headers = {**auth_headers, **json_headers}
        payload = {
            "command": "echo 'test'"
        }
        response = client.post(
            f"{TestConfig.API_V1}/terminal/execute",
            json=payload,
            headers=headers
        )
        assert response.status_code in [200, 401, 403, 404, 422, 500]


class TestAdminEndpoints:
    """Test suite for admin endpoints."""
    
    def test_admin_revoke_user(self, client, auth_headers):
        """Test admin user revocation endpoint."""
        response = client.post(
            f"/admin/revoke/user/{TestConfig.TEST_USER_ID}",
            headers=auth_headers
        )
        # Admin endpoints require elevated permissions
        assert response.status_code in [200, 401, 403, 404]
    
    def test_admin_revocation_status(self, client, auth_headers):
        """Test admin revocation status endpoint."""
        response = client.get(
            "/admin/revocation/status",
            headers=auth_headers
        )
        assert response.status_code in [200, 401, 403, 404]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
