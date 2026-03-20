"""Authentication verification cache to reduce database load.

Author: Resonant Genesis Team
Updated: January 11, 2026
"""

import time
import hashlib
from typing import Dict, Optional, Any
from dataclasses import dataclass

@dataclass
class CacheEntry:
    """Cache entry with TTL."""
    data: Any
    expires_at: float
    
    def is_expired(self) -> bool:
        return time.time() > self.expires_at

class AuthCache:
    """LRU cache for authentication verification results."""
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 60):
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.cache: Dict[str, CacheEntry] = {}
        self.access_order: list = []  # Track LRU order
        
    def _generate_key(self, token: str) -> str:
        """Generate cache key from token."""
        # Use hash of token for security and to avoid storing raw tokens
        return hashlib.sha256(token.encode()).hexdigest()[:16]
    
    def _evict_if_needed(self):
        """Evict oldest entries if cache is full."""
        while len(self.cache) >= self.max_size:
            # Remove oldest (LRU)
            oldest_key = self.access_order.pop(0)
            if oldest_key in self.cache:
                del self.cache[oldest_key]
    
    def _update_access_order(self, key: str):
        """Update LRU access order."""
        if key in self.access_order:
            self.access_order.remove(key)
        self.access_order.append(key)
    
    def get(self, token: str) -> Optional[Any]:
        """Get cached auth verification result."""
        key = self._generate_key(token)
        
        if key not in self.cache:
            return None
            
        entry = self.cache[key]
        
        # Check if expired
        if entry.is_expired():
            del self.cache[key]
            if key in self.access_order:
                self.access_order.remove(key)
            return None
            
        # Update access order
        self._update_access_order(key)
        
        return entry.data
    
    def set(self, token: str, data: Any):
        """Cache auth verification result."""
        key = self._generate_key(token)
        
        # Evict if needed
        self._evict_if_needed()
        
        # Store new entry
        expires_at = time.time() + self.ttl_seconds
        self.cache[key] = CacheEntry(data=data, expires_at=expires_at)
        
        # Update access order
        self._update_access_order(key)
    
    def invalidate(self, token: str = None, user_id: str = None):
        """Invalidate cache entries."""
        if token:
            # Invalidate specific token
            key = self._generate_key(token)
            if key in self.cache:
                del self.cache[key]
                if key in self.access_order:
                    self.access_order.remove(key)
        elif user_id:
            # Invalidate all tokens for user
            keys_to_remove = []
            for key, entry in self.cache.items():
                if isinstance(entry.data, dict) and entry.data.get("user_id") == user_id:
                    keys_to_remove.append(key)
            
            for key in keys_to_remove:
                del self.cache[key]
                if key in self.access_order:
                    self.access_order.remove(key)
        else:
            # Clear all cache
            self.cache.clear()
            self.access_order.clear()
    
    def cleanup_expired(self):
        """Remove expired entries."""
        current_time = time.time()
        expired_keys = []
        
        for key, entry in self.cache.items():
            if current_time > entry.expires_at:
                expired_keys.append(key)
        
        for key in expired_keys:
            del self.cache[key]
            if key in self.access_order:
                self.access_order.remove(key)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "ttl_seconds": self.ttl_seconds,
            "hit_rate": getattr(self, "_hit_count", 0) / max(1, getattr(self, "_total_requests", 1))
        }

# Global cache instance
auth_cache = AuthCache(max_size=1000, ttl_seconds=60)  # 1 minute TTL
