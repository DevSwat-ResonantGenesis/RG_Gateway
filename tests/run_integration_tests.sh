#!/bin/bash
# API Integration Test Runner
# Author: Agent 7 - ResonantGenesis Team
# Created: February 21, 2026

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "ResonantGenesis API Integration Tests"
echo "=========================================="
echo ""

# Set test environment
export TESTING=true
export PYTHONPATH="$PROJECT_ROOT:$PROJECT_ROOT/gateway:$PYTHONPATH"

# Check if pytest is installed
if ! command -v pytest &> /dev/null; then
    echo "Installing pytest..."
    pip install pytest pytest-asyncio httpx
fi

# Run tests with different verbosity options
case "${1:-default}" in
    "verbose"|"-v")
        echo "Running tests in verbose mode..."
        pytest "$SCRIPT_DIR" -v --tb=short
        ;;
    "quiet"|"-q")
        echo "Running tests in quiet mode..."
        pytest "$SCRIPT_DIR" -q
        ;;
    "coverage"|"-c")
        echo "Running tests with coverage..."
        pip install pytest-cov 2>/dev/null || true
        pytest "$SCRIPT_DIR" -v --cov=app --cov-report=term-missing
        ;;
    "fast"|"-f")
        echo "Running fast tests only (excluding slow markers)..."
        pytest "$SCRIPT_DIR" -v -m "not slow"
        ;;
    "auth")
        echo "Running authentication tests only..."
        pytest "$SCRIPT_DIR" -v -k "Auth"
        ;;
    "blockchain")
        echo "Running blockchain tests only..."
        pytest "$SCRIPT_DIR" -v -k "Blockchain or Contract"
        ;;
    "webhook")
        echo "Running webhook tests only..."
        pytest "$SCRIPT_DIR" -v -k "Webhook"
        ;;
    "health")
        echo "Running health/status tests only..."
        pytest "$SCRIPT_DIR" -v -k "Health or Status"
        ;;
    *)
        echo "Running all tests..."
        pytest "$SCRIPT_DIR" -v --tb=short
        ;;
esac

echo ""
echo "=========================================="
echo "Tests completed!"
echo "=========================================="
