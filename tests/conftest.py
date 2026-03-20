"""Pytest configuration and shared fixtures for API integration tests.

Author: Agent 7 - ResonantGenesis Team
Created: February 21, 2026
"""

import pytest
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent directories to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="session")
def test_env():
    """Set up test environment variables."""
    original_env = os.environ.copy()
    
    # Set test environment variables
    os.environ["TESTING"] = "true"
    os.environ["DATABASE_URL"] = "sqlite:///./test.db"
    os.environ["REDIS_URL"] = "redis://localhost:6379/15"
    os.environ["JWT_SECRET"] = "test-jwt-secret-key-for-testing"
    os.environ["API_KEY"] = "test-api-key"
    
    yield
    
    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


@pytest.fixture
def mock_redis():
    """Mock Redis client for tests."""
    mock = MagicMock()
    mock.get.return_value = None
    mock.set.return_value = True
    mock.delete.return_value = True
    mock.incr.return_value = 1
    mock.expire.return_value = True
    return mock


@pytest.fixture
def mock_database():
    """Mock database session for tests."""
    mock = MagicMock()
    mock.query.return_value = mock
    mock.filter.return_value = mock
    mock.first.return_value = None
    mock.all.return_value = []
    mock.add.return_value = None
    mock.commit.return_value = None
    mock.rollback.return_value = None
    return mock


@pytest.fixture
def mock_blockchain_client():
    """Mock blockchain client for tests."""
    mock = MagicMock()
    mock.get_block_number.return_value = 12345678
    mock.get_balance.return_value = 1000000000000000000
    mock.send_transaction.return_value = {"hash": "0x" + "a" * 64}
    mock.call_contract.return_value = {"result": "success"}
    return mock


@pytest.fixture
def sample_user_data():
    """Sample user data for tests."""
    return {
        "id": "test-user-123",
        "email": "test@example.com",
        "username": "testuser",
        "created_at": "2026-02-21T00:00:00Z"
    }


@pytest.fixture
def sample_agent_data():
    """Sample agent data for tests."""
    return {
        "id": "test-agent-456",
        "name": "Test Agent",
        "owner_id": "test-user-123",
        "status": "active",
        "manifest_hash": "0x" + "b" * 64,
        "metadata_uri": "ipfs://test-metadata",
        "created_at": "2026-02-21T00:00:00Z"
    }


@pytest.fixture
def sample_identity_data():
    """Sample identity data for tests."""
    return {
        "dsid": "0x" + "c" * 64,
        "user_hash": "test-hash-123",
        "public_key": "0x" + "d" * 64,
        "status": "active",
        "created_at": "2026-02-21T00:00:00Z"
    }


@pytest.fixture
def valid_jwt_token():
    """Generate a valid JWT token for testing."""
    import jwt
    import time
    
    payload = {
        "sub": "test-user-123",
        "exp": int(time.time()) + 3600,
        "iat": int(time.time()),
        "type": "access"
    }
    
    secret = os.environ.get("JWT_SECRET", "test-jwt-secret-key-for-testing")
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token


@pytest.fixture
def expired_jwt_token():
    """Generate an expired JWT token for testing."""
    import jwt
    import time
    
    payload = {
        "sub": "test-user-123",
        "exp": int(time.time()) - 3600,  # Expired 1 hour ago
        "iat": int(time.time()) - 7200,
        "type": "access"
    }
    
    secret = os.environ.get("JWT_SECRET", "test-jwt-secret-key-for-testing")
    token = jwt.encode(payload, secret, algorithm="HS256")
    return token


def pytest_configure(config):
    """Configure pytest with custom markers."""
    config.addinivalue_line(
        "markers", "integration: mark test as integration test"
    )
    config.addinivalue_line(
        "markers", "slow: mark test as slow running"
    )
    config.addinivalue_line(
        "markers", "auth: mark test as authentication related"
    )
    config.addinivalue_line(
        "markers", "blockchain: mark test as blockchain related"
    )


def pytest_collection_modifyitems(config, items):
    """Modify test collection to add markers based on test names."""
    for item in items:
        # Add integration marker to all tests in this directory
        item.add_marker(pytest.mark.integration)
        
        # Add specific markers based on test class names
        if "Auth" in item.nodeid:
            item.add_marker(pytest.mark.auth)
        if "Blockchain" in item.nodeid or "Contract" in item.nodeid:
            item.add_marker(pytest.mark.blockchain)
