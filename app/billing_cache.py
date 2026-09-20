"""
Billing Cache - Redis integration for plan/credit status.

The Billing Service updates this cache on subscription changes.
The Gateway reads from this cache to inject plan headers.
"""

import json
import logging
from typing import Optional, Dict, Any
import redis.asyncio as redis

from .config import settings

logger = logging.getLogger(__name__)


class BillingCache:
    """Redis client for billing plan/credit status."""
    
    def __init__(self):
        self.redis: Optional[redis.Redis] = None
        self._enabled = False
    
    async def connect(self):
        """Connect to Redis."""
        try:
            self.redis = redis.from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
            await self.redis.ping()
            self._enabled = True
            logger.info("[BillingCache] Connected to Redis")
        except Exception as e:
            logger.warning(f"[BillingCache] Redis connection failed: {e}")
            self._enabled = False
    
    async def disconnect(self):
        """Disconnect from Redis."""
        if self.redis:
            await self.redis.close()
            self._enabled = False
    
    async def get_plan_status(self, org_id: str) -> Optional[Dict[str, Any]]:
        """
        Get plan status from Redis cache.

        Returns:
            {
                "plan": "free|plus|max|teams|enterprise",
                "credits_remaining": 50000,
                "credits_exhausted": false,
                "subscription_status": "active|past_due|canceled|suspended",
            }
        """
        if not self._enabled or not self.redis:
            return None

        try:
            key = f"billing:org:{org_id}"
            data = await self.redis.get(key)
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"[BillingCache] Failed to get plan status: {e}")

        return None
    
    async def set_plan_status(self, org_id: str, data: Dict[str, Any], ttl: int = 300):
        """
        Set plan status in Redis cache.
        
        Called by Billing Service on subscription changes.
        """
        if not self._enabled or not self.redis:
            return
        
        try:
            key = f"billing:org:{org_id}"
            await self.redis.setex(key, ttl, json.dumps(data))
            logger.info(f"[BillingCache] Updated plan status for org {org_id}: {data.get('plan')}")
        except Exception as e:
            logger.error(f"[BillingCache] Failed to set plan status: {e}")


# Global instance
billing_cache = BillingCache()
