"""OpenAPI Governance and Route Collision Detection.

Provides automated route collision testing and OpenAPI snapshot management.
"""

import json
import hashlib
from datetime import datetime
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict


class RouteCollisionDetector:
    """Detects route collisions in FastAPI applications."""
    
    def __init__(self):
        self.routes: List[Dict] = []
        self.collisions: List[Dict] = []
    
    def add_route(self, path: str, methods: List[str], name: str):
        """Add a route for collision detection."""
        self.routes.append({
            "path": path,
            "methods": methods,
            "name": name,
        })
    
    def _normalize_path(self, path: str) -> str:
        """Normalize path for comparison (replace path params with placeholder)."""
        import re
        # Replace {param} with {*}
        return re.sub(r'\{[^}]+\}', '{*}', path)
    
    def detect_collisions(self) -> List[Dict]:
        """Detect route collisions."""
        self.collisions = []
        
        # Group routes by normalized path
        path_groups: Dict[str, List[Dict]] = defaultdict(list)
        
        for route in self.routes:
            normalized = self._normalize_path(route["path"])
            path_groups[normalized].append(route)
        
        # Check for method overlaps within same normalized path
        for normalized_path, routes in path_groups.items():
            if len(routes) > 1:
                # Check for method overlaps
                method_routes: Dict[str, List[Dict]] = defaultdict(list)
                for route in routes:
                    for method in route["methods"]:
                        method_routes[method].append(route)
                
                for method, conflicting in method_routes.items():
                    if len(conflicting) > 1:
                        self.collisions.append({
                            "type": "method_collision",
                            "normalized_path": normalized_path,
                            "method": method,
                            "routes": [r["path"] for r in conflicting],
                            "names": [r["name"] for r in conflicting],
                        })
        
        return self.collisions
    
    def get_report(self) -> Dict:
        """Get collision detection report."""
        collisions = self.detect_collisions()
        
        return {
            "total_routes": len(self.routes),
            "collision_count": len(collisions),
            "collisions": collisions,
            "status": "clean" if not collisions else "has_collisions",
            "checked_at": datetime.utcnow().isoformat(),
        }


class OpenAPISnapshot:
    """Manages OpenAPI schema snapshots for breaking change detection."""
    
    def __init__(self):
        self.snapshots: Dict[str, Dict] = {}
    
    def create_snapshot(self, version: str, schema: Dict) -> str:
        """Create a snapshot of the OpenAPI schema."""
        snapshot_hash = hashlib.sha256(
            json.dumps(schema, sort_keys=True).encode()
        ).hexdigest()[:16]
        
        self.snapshots[version] = {
            "schema": schema,
            "hash": snapshot_hash,
            "created_at": datetime.utcnow().isoformat(),
        }
        
        return snapshot_hash
    
    def compare_schemas(self, old_version: str, new_schema: Dict) -> Dict:
        """Compare new schema against a previous snapshot."""
        if old_version not in self.snapshots:
            return {"error": f"Snapshot {old_version} not found"}
        
        old_schema = self.snapshots[old_version]["schema"]
        
        breaking_changes = []
        additions = []
        
        old_paths = set(old_schema.get("paths", {}).keys())
        new_paths = set(new_schema.get("paths", {}).keys())
        
        # Removed paths are breaking changes
        removed_paths = old_paths - new_paths
        for path in removed_paths:
            breaking_changes.append({
                "type": "path_removed",
                "path": path,
            })
        
        # Added paths are additions
        added_paths = new_paths - old_paths
        for path in added_paths:
            additions.append({
                "type": "path_added",
                "path": path,
            })
        
        # Check for method changes in existing paths
        for path in old_paths & new_paths:
            old_methods = set(old_schema["paths"][path].keys())
            new_methods = set(new_schema["paths"][path].keys())
            
            removed_methods = old_methods - new_methods
            for method in removed_methods:
                breaking_changes.append({
                    "type": "method_removed",
                    "path": path,
                    "method": method,
                })
        
        return {
            "breaking_changes": breaking_changes,
            "additions": additions,
            "is_breaking": len(breaking_changes) > 0,
            "compared_at": datetime.utcnow().isoformat(),
        }


# Global instances
route_detector = RouteCollisionDetector()
openapi_snapshots = OpenAPISnapshot()


def check_routes_from_app(app) -> Dict:
    """Check routes from a FastAPI app for collisions."""
    detector = RouteCollisionDetector()
    
    for route in app.routes:
        if hasattr(route, 'path') and hasattr(route, 'methods'):
            detector.add_route(
                path=route.path,
                methods=list(route.methods) if route.methods else ["GET"],
                name=route.name or "unnamed",
            )
    
    return detector.get_report()


def create_openapi_snapshot(app, version: str) -> str:
    """Create an OpenAPI snapshot from a FastAPI app."""
    schema = app.openapi()
    return openapi_snapshots.create_snapshot(version, schema)


def compare_with_snapshot(app, old_version: str) -> Dict:
    """Compare current app schema with a previous snapshot."""
    new_schema = app.openapi()
    return openapi_snapshots.compare_schemas(old_version, new_schema)
