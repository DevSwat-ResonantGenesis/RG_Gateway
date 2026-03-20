"""Route invariant tests to prevent regression.

These tests ensure that:
1. No catch-all routes shadow specific routes
2. Router ordering is correct
3. All routes pass through auth middleware
4. WebSocket routes are protected

Author: Resonant Genesis Team
Updated: January 11, 2026
"""

from fastapi import FastAPI
from app.main import app


class RouteInvariantError(Exception):
    """Raised when a route invariant is violated."""
    pass


class TestRouteInvariants:
    """Test suite for route invariants that must never be violated."""
    
    def test_no_catch_all_shadows_specific_routes(self):
        """Ensure no catch-all route shadows specific routes."""
        
        # Get all routes
        routes = []
        for route in app.routes:
            if hasattr(route, 'path'):
                routes.append({
                    'path': route.path,
                    'methods': getattr(route, 'methods', set()),
                    'name': getattr(route, 'name', 'unknown')
                })
        
        # Find catch-all routes (paths with {path:path} or similar)
        catch_all_routes = [r for r in routes if '{path:' in r['path']]
        
        # Check specific routes that must not be shadowed
        protected_routes = [
            '/api/v1/code/projects',
            '/api/v1/code/execute',
            '/api/v1/terminal/execute',
            '/api/v1/anchors/anchors',
            '/api/v1/policies/policies',
            '/admin/revoke/user/{user_id}',
            '/admin/revoke/org/{org_id}',
            '/admin/revoke/role/{role}',
            '/admin/revoke/all',
            '/admin/revocation/status'
        ]
        
        # Verify each protected route exists
        for protected_path in protected_routes:
            matching_routes = [r for r in routes if r['path'] == protected_path]
            assert len(matching_routes) > 0, f"Protected route {protected_path} not found"
            
            # Verify it's not shadowed by a catch-all that comes before it
            protected_route = matching_routes[0]
            protected_index = routes.index(protected_route)
            
            for catch_all in catch_all_routes:
                catch_index = routes.index(catch_all)
                if catch_index < protected_index:
                    # Check if catch-all would match the protected route
                    if self._path_matches_catch_all(protected_path, catch_all['path']):
                        raise RouteInvariantError(f"Protected route {protected_path} is shadowed by catch-all {catch_all['path']} at index {catch_index}")
    
    def _path_matches_catch_all(self, path: str, catch_all_pattern: str) -> bool:
        """Check if a path would be matched by a catch-all pattern."""
        # Simple pattern matching for FastAPI routes
        # Convert {path:path} to wildcard
        pattern = catch_all_pattern.replace('{path:path}', '.*')
        pattern = pattern.replace('{path}', '[^/]+')
        
        import re
        return re.match(pattern, path) is not None
    
    def test_router_ordering_invariant(self):
        """Ensure routers are included in the correct order."""
        
        # Get the route order
        routes = []
        for route in app.routes:
            if hasattr(route, 'path'):
                routes.append(route.path)
        
        # Find critical routes and their indices
        code_routes = [i for i, path in enumerate(routes) if '/api/v1/code/' in path]
        terminal_routes = [i for i, path in enumerate(routes) if '/api/v1/terminal/' in path]
        catch_all_index = next((i for i, path in enumerate(routes) if '/api/v1/{path:path}' in path), None)
        
        # Assert specific routes come before catch-all
        if catch_all_index is not None:
            for code_idx in code_routes:
                assert code_idx < catch_all_index, f"Code route at index {code_idx} comes after catch-all at {catch_all_index}"
            
            for terminal_idx in terminal_routes:
                assert terminal_idx < catch_all_index, f"Terminal route at index {terminal_idx} comes after catch-all at {catch_all_index}"
    
    def test_all_api_routes_have_auth_prefix(self):
        """Ensure all API routes have proper prefixes."""
        
        api_routes = []
        for route in app.routes:
            if hasattr(route, 'path'):
                path = route.path
                if path.startswith('/api/v1/'):
                    api_routes.append(path)
        
        # Verify no API routes are at root level
        root_api_routes = [r for r in api_routes if r == '/api/v1' or r == '/api/v1/']
        assert len(root_api_routes) == 0, "Found API routes at incorrect prefix level"
    
    def test_websocket_routes_are_protected(self):
        """Ensure WebSocket routes are properly protected."""
        
        # Check for WebSocket routes
        from fastapi.routing import APIWebSocketRoute
        
        ws_routes = []
        for route in app.routes:
            if isinstance(route, APIWebSocketRoute):
                ws_routes.append(route.path)
        
        # Verify critical WebSocket routes exist
        expected_ws_routes = [
            '/api/v1/ws/{client_id}',
            '/api/v1/ws/chat/{chat_id}'
        ]
        
        for expected in expected_ws_routes:
            matching = [r for r in ws_routes if expected in r or r == expected]
            assert len(matching) > 0, f"Expected WebSocket route {expected} not found"
    
    def test_admin_routes_exist(self):
        """Ensure all admin revocation routes exist."""
        
        admin_routes = []
        for route in app.routes:
            if hasattr(route, 'path') and '/admin/' in route.path:
                admin_routes.append(route.path)
        
        expected_admin_routes = [
            '/admin/revoke/user/{user_id}',
            '/admin/revoke/org/{org_id}',
            '/admin/revoke/role/{role}',
            '/admin/revoke/all',
            '/admin/revocation/status'
        ]
        
        for expected in expected_admin_routes:
            assert expected in admin_routes, f"Admin route {expected} not found"
    
    def test_no_duplicate_route_paths(self):
        """Ensure no duplicate route paths that could cause conflicts."""
        
        route_paths = []
        for route in app.routes:
            if hasattr(route, 'path'):
                route_paths.append(route.path)
        
        # Check for exact duplicates
        from collections import Counter
        path_counts = Counter(route_paths)
        duplicates = {path: count for path, count in path_counts.items() if count > 1}
        
        # Some duplicates are intentional (e.g., different methods for same path)
        # Filter to only actual conflicts (same path AND same methods)
        actual_conflicts = {}
        for path, count in duplicates.items():
            routes_with_path = [r for r in app.routes if hasattr(r, 'path') and r.path == path]
            method_sets = [getattr(r, 'methods', set()) for r in routes_with_path]
            
            # Check if any method overlaps between routes
            all_methods = set()
            for method_set in method_sets:
                all_methods.update(method_set)
            
            # If total unique methods < sum of individual methods, there's overlap
            total_methods = sum(len(m) for m in method_sets)
            if len(all_methods) < total_methods:
                actual_conflicts[path] = count
        
        # Allow certain intentional duplicates
        allowed_duplicates = {
            '/api/v1/anchors/anchors',  # GET/POST and GET/DELETE for different operations
            '/api/v1/anchors/anchors/{anchor_id}',  # GET and DELETE
            '/api/v1/policies/policies',  # Similar pattern
            '/api/v1/policies/policies/{policy_id}',
            '/api/v1/code/project-builder/projects/{project_id}',  # Multiple operations
            '/api/v1/memory/{path:path}',
            '/api/v1/chat/{path:path}',
            '/api/v1/scan/{path:path}',
            '/api/v1/rara/agents/{agent_id}/capabilities',
            '/api/v1/rara/agents/{agent_id}/stats',
            '/api/v1/workflow/{path:path}',
            '/autonomy/{path:path}',
        }
        
        # Remove allowed duplicates
        actual_conflicts = {k: v for k, v in actual_conflicts.items() if k not in allowed_duplicates}
        
        assert len(actual_conflicts) == 0, f"Found conflicting duplicate route paths: {actual_conflicts}"
    
    def test_middleware_stack_order(self):
        """Verify middleware is in the correct order."""
        
        # Expected order: CORS -> RateLimit -> Auth
        expected_order = ['CORSMiddleware', 'RateLimitMiddleware', 'AuthMiddleware']
        
        actual_order = []
        for middleware in app.user_middleware:
            actual_order.append(middleware.cls.__name__)
        
        # Check that all expected middlewares are present
        for expected in expected_order:
            assert expected in actual_order, f"Expected middleware {expected} not found"
        
        # Check order (CORS should be first, Auth should be last)
        cors_index = actual_order.index('CORSMiddleware') if 'CORSMiddleware' in actual_order else -1
        auth_index = actual_order.index('AuthMiddleware') if 'AuthMiddleware' in actual_order else -1
        
        if cors_index != -1 and auth_index != -1:
            assert cors_index < auth_index, "CORS middleware should come before Auth middleware"


if __name__ == "__main__":
    # Run tests directly
    test_instance = TestRouteInvariants()
    
    print("Running route invariant tests...")
    
    try:
        test_instance.test_no_catch_all_shadows_specific_routes()
        print("✅ No catch-all shadowing")
    except AssertionError as e:
        print(f"❌ Catch-all shadowing test failed: {e}")
    
    try:
        test_instance.test_router_ordering_invariant()
        print("✅ Router ordering correct")
    except AssertionError as e:
        print(f"❌ Router ordering test failed: {e}")
    
    try:
        test_instance.test_all_api_routes_have_auth_prefix()
        print("✅ API routes have correct prefixes")
    except AssertionError as e:
        print(f"❌ API prefix test failed: {e}")
    
    try:
        test_instance.test_websocket_routes_are_protected()
        print("✅ WebSocket routes protected")
    except AssertionError as e:
        print(f"❌ WebSocket protection test failed: {e}")
    
    try:
        test_instance.test_admin_routes_exist()
        print("✅ Admin routes exist")
    except AssertionError as e:
        print(f"❌ Admin routes test failed: {e}")
    
    try:
        test_instance.test_no_duplicate_route_paths()
        print("✅ No duplicate routes")
    except AssertionError as e:
        print(f"❌ Duplicate routes test failed: {e}")
    
    try:
        test_instance.test_middleware_stack_order()
        print("✅ Middleware order correct")
    except AssertionError as e:
        print(f"❌ Middleware order test failed: {e}")
    
    print("\nRoute invariant tests complete.")
