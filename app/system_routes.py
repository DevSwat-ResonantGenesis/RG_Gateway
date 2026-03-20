"""
Owner Dashboard - System Metrics Routes
Provides real-time system metrics, service health, database stats,
user data, analytics, activity logs, V8 engine data, and RARA agents
for the owner dashboard.
"""
import os
import time
import logging
import asyncio
import socket
import httpx
import psutil
import ssl
import asyncpg
from datetime import datetime
from typing import Dict, Any, List
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/owner/dashboard/system", tags=["Owner Dashboard - System"])

# All 28 Docker services with correct ports
SERVICE_URLS = {
    "gateway": ("Gateway", "http://localhost:8001"),
    "auth_service": ("Auth Service", "http://auth_service:8000"),
    "user_service": ("User Service", "http://user_service:8000"),
    "chat_service": ("Chat Service", "http://chat_service:8000"),
    "billing_service": ("Billing Service", "http://billing_service:8000"),
    "memory_service": ("Memory Service", "http://memory_service:8000"),
    "rg_internal_invarients_sim": ("RARA Governance", "http://rg_internal_invarients_sim:8093"),
    "agent_engine_service": ("Agent Engine", "http://agent_engine_service:8000"),
    "llm_service": ("LLM Service", "http://llm_service:8000"),
    "v8_api_service": ("V8 Engine", "http://v8_api_service:8080"),
    "ml_service": ("ML Service", "http://ml_service:8000"),
    "blockchain_service": ("Blockchain Service", "http://blockchain_service:8000"),
    "blockchain_node": ("Blockchain Node", "http://blockchain_node:8000"),
    "rg_users_invarients_sim": ("Users Invariants SIM", "http://rg_users_invarients_sim:8091"),
    "marketplace_service": ("Marketplace", "http://marketplace_service:8000"),
    "notification_service": ("Notifications", "http://notification_service:8000"),
    "workflow_service": ("Workflow Engine", "http://workflow_service:8000"),
    "storage_service": ("Storage Service", "http://storage_service:8000"),
    "sandbox_runner_service": ("Sandbox Runner", "http://sandbox_runner_service:8000"),
    "cognitive_service": ("Cognitive Service", "http://cognitive_service:8000"),
    "user_memory_service": ("User Memory", "http://user_memory_service:8000"),
    "crypto_service": ("Crypto Service", "http://crypto_service:8000"),
    "code_execution_service": ("Code Execution", "http://code_execution_service:8000"),
    "build_service": ("Build Service", "http://build_service:8003"),
    "ide_platform_service": ("IDE Platform Service", "http://ide_platform_service:8080"),
    "ed_service": ("ED Service", "http://ed_service:8000"),
    "rg_ast_analysis": ("AST Analysis", "http://rg_ast_analysis:8000"),
    "shared_redis": ("Redis Cache", "redis://shared_redis:6379"),
}

V8_DEV_TOKEN = os.getenv("V8_DEV_TOKEN", "LouieArt")
AUTH_DB_URL = os.getenv("AUTH_DATABASE_URL", os.getenv("DATABASE_URL", ""))

# Convert asyncpg URL format: remove +asyncpg suffix and strip ssl param (we set it explicitly)
_pg_dsn = AUTH_DB_URL.replace("postgresql+asyncpg://", "postgresql://")
if "?" in _pg_dsn:
    _pg_dsn = _pg_dsn.split("?")[0]


# Persistent connection pool (lazy-initialized)
_db_pool: asyncpg.Pool | None = None
_db_pool_lock = asyncio.Lock()


async def _get_db_pool() -> asyncpg.Pool:
    """Get or create a shared asyncpg connection pool to auth database."""
    global _db_pool
    if _db_pool is not None and not _db_pool._closed:
        return _db_pool
    async with _db_pool_lock:
        if _db_pool is not None and not _db_pool._closed:
            return _db_pool
        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        _db_pool = await asyncpg.create_pool(
            _pg_dsn, ssl=ssl_ctx, min_size=0, max_size=2, timeout=10,
            command_timeout=15,
        )
        logger.info("Gateway DB connection pool created (min=0, max=2)")
        return _db_pool


async def _get_db_connection():
    """Acquire a connection from the shared pool."""
    pool = await _get_db_pool()
    return await pool.acquire()


def _format_uptime(seconds: float) -> str:
    """Format seconds to human-readable uptime string."""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    if days > 0:
        return f"{days}d {hours}h {minutes}m"
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


async def _check_service_health(key: str, name: str, url: str) -> Dict[str, Any]:
    """Check service health with actual latency measurement."""
    start = time.time()
    # Redis uses TCP PING protocol
    if url.startswith("redis://"):
        try:
            host = url.replace("redis://", "").split(":")[0]
            port = int(url.split(":")[-1])
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((host, port))
            s.send(b"PING\r\n")
            data = s.recv(64)
            s.close()
            latency = round((time.time() - start) * 1000)
            if b"PONG" in data:
                return {"key": key, "name": name, "status": "healthy", "latency": latency, "online": True}
            return {"key": key, "name": name, "status": "degraded", "latency": latency, "online": True}
        except Exception as e:
            latency = round((time.time() - start) * 1000)
            return {"key": key, "name": name, "status": "offline", "latency": latency, "online": False, "error": str(e)[:100]}
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/health")
            latency = round((time.time() - start) * 1000)
            if resp.status_code == 200:
                return {"key": key, "name": name, "status": "healthy", "latency": latency, "online": True}
            return {"key": key, "name": name, "status": "degraded", "latency": latency, "online": True, "code": resp.status_code}
    except Exception as e:
        latency = round((time.time() - start) * 1000)
        return {"key": key, "name": name, "status": "offline", "latency": latency, "online": False, "error": str(e)[:100]}


# ============================================
# SYSTEM METRICS
# ============================================

@router.get("/metrics")
async def get_system_metrics():
    """Real system metrics - CPU, memory, disk, network via psutil."""
    try:
        cpu_percent = psutil.cpu_percent(interval=0.5)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net = psutil.net_io_counters()
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        return {
            "cpu": {
                "usage_percent": round(cpu_percent, 1),
                "cores": psutil.cpu_count(),
                "load_avg": [round(x, 2) for x in os.getloadavg()] if hasattr(os, "getloadavg") else [],
            },
            "memory": {
                "total_gb": round(memory.total / (1024**3), 1),
                "used_gb": round(memory.used / (1024**3), 1),
                "available_gb": round(memory.available / (1024**3), 1),
                "usage_percent": round(memory.percent, 1),
            },
            "disk": {
                "total_gb": round(disk.total / (1024**3), 1),
                "used_gb": round(disk.used / (1024**3), 1),
                "free_gb": round(disk.free / (1024**3), 1),
                "usage_percent": round(disk.percent, 1),
            },
            "network": {
                "bytes_sent": net.bytes_sent,
                "bytes_recv": net.bytes_recv,
                "packets_sent": net.packets_sent,
                "packets_recv": net.packets_recv,
            },
            "uptime_seconds": round(uptime_seconds),
            "uptime_human": _format_uptime(uptime_seconds),
            "timestamp": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        logger.error(f"Failed to get system metrics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================
# ALL 28 SERVICES HEALTH
# ============================================

@router.get("/services")
async def get_all_services():
    """Health check for ALL 28 Docker services with real latency."""
    tasks = [_check_service_health(k, v[0], v[1]) for k, v in SERVICE_URLS.items()]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    services = []
    for r in results:
        if isinstance(r, Exception):
            services.append({"key": "unknown", "name": "Unknown", "status": "error", "latency": 0, "online": False, "error": str(r)[:100]})
        else:
            services.append(r)
    services = sorted(services, key=lambda s: (0 if s["status"] == "healthy" else 1 if s["status"] == "degraded" else 2, s["name"]))
    return {
        "services": services,
        "total": len(services),
        "healthy": sum(1 for s in services if s["status"] == "healthy"),
        "degraded": sum(1 for s in services if s["status"] == "degraded"),
        "offline": sum(1 for s in services if s["status"] in ("offline", "error")),
        "timestamp": datetime.utcnow().isoformat(),
    }


# ============================================
# PLATFORM USERS (proxied from auth_service DB)
# ============================================

@router.get("/users")
async def get_platform_users(request: Request):
    """Get real platform users directly from PostgreSQL database.
    Uses asyncpg for direct DB access - no JWT proxy needed."""
    auth_header = request.headers.get("Authorization", "")
    pool = None
    conn = None
    try:
        pool = await _get_db_pool()
        conn = await pool.acquire()
        rows = await conn.fetch("""
            SELECT u.id, u.email, u.username, u.full_name, u.status, u.is_active, u.is_superuser,
                   u.mfa_enabled, u.email_verified, u.unlimited_credits, u.trial_expires_at,
                   u.last_login_at, u.created_at, u.updated_at,
                   COALESCE(chat_counts.chat_count, 0) AS chat_count,
                   COALESCE(chat_counts.message_count, 0) AS message_count
            FROM users u
            LEFT JOIN LATERAL (
                SELECT COUNT(DISTINCT rc.id) AS chat_count,
                       (SELECT COUNT(*) FROM resonant_chat_messages rcm
                        WHERE rcm.chat_id IN (SELECT rc2.id FROM resonant_chats rc2 WHERE rc2.user_id = u.id::text)
                       ) AS message_count
                FROM resonant_chats rc WHERE rc.user_id = u.id::text
            ) chat_counts ON true
            ORDER BY u.created_at DESC LIMIT 500
        """)
        total = await conn.fetchval("SELECT COUNT(*) FROM users")
        # Recent registrations (today)
        today_count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE"
        )
        week_count = await conn.fetchval(
            "SELECT COUNT(*) FROM users WHERE created_at >= CURRENT_DATE - INTERVAL '7 days'"
        )
        users = []
        for row in rows:
            trial_exp = row["trial_expires_at"]
            trial_status = None
            if trial_exp:
                from datetime import datetime, timezone
                now = datetime.now(timezone.utc)
                if trial_exp.tzinfo is None:
                    from datetime import timezone as tz
                    trial_exp = trial_exp.replace(tzinfo=tz.utc)
                if now < trial_exp:
                    days_left = (trial_exp - now).days
                    trial_status = f"active ({days_left}d left)"
                else:
                    trial_status = "expired"
            users.append({
                "id": str(row["id"]),
                "email": row["email"] or "",
                "username": row["username"] or "",
                "full_name": row["full_name"] or "",
                "status": row["status"] or ("active" if row["is_active"] else "inactive"),
                "is_active": row["is_active"],
                "is_superuser": row["is_superuser"],
                "unlimited_credits": row["unlimited_credits"] if row["unlimited_credits"] is not None else False,
                "trial_expires_at": trial_exp.isoformat() if trial_exp else None,
                "trial_status": trial_status,
                "mfa_enabled": row["mfa_enabled"] if row["mfa_enabled"] is not None else False,
                "email_verified": row["email_verified"] if row["email_verified"] is not None else False,
                "last_login_at": row["last_login_at"].isoformat() if row["last_login_at"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
                "chat_count": row["chat_count"],
                "message_count": row["message_count"],
            })
        if users or (total or 0) > 0:
            return {
                "users": users,
                "total": total or 0,
                "registrations_today": today_count or 0,
                "registrations_7d": week_count or 0,
            }
    except Exception as e:
        logger.error(f"Failed to fetch users from DB: {e}")
    finally:
        if conn and pool:
            await pool.release(conn)

    if auth_header:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(
                    "http://auth_service:8000/owner/auth/dashboard/users",
                    headers={"Authorization": auth_header},
                )
                if resp.status_code == 200:
                    return resp.json()
        except Exception as ex:
            logger.error(f"Auth service fallback for users failed: {ex}")

    return {"users": [], "total": 0, "error": "gateway_user_db_unavailable"}


# ============================================
# PLATFORM ANALYTICS (credits, API calls, conversion)
# ============================================

@router.get("/analytics")
async def get_platform_analytics(request: Request):
    """Real platform analytics - credits consumed, API calls, conversion rate."""
    auth_header = request.headers.get("Authorization", "")
    analytics = {
        "credits_consumed": None,
        "api_calls_30d": None,
        "api_calls_24h": None,
        "conversion_rate": None,
        "total_users": None,
        "active_users_24h": None,
        "paid_users": None,
        "paying_users": None,
        "revenue_30d": None,
        "avg_response_time_ms": None,
        "error_rate_24h": None,
        "active_connections": None,
    }
    try:
        # Get user stats directly from DB
        pool = None
        conn = None
        try:
            pool = await _get_db_pool()
            conn = await pool.acquire()
            analytics["total_users"] = await conn.fetchval("SELECT COUNT(*) FROM users")
            analytics["active_users_24h"] = await conn.fetchval(
                "SELECT COUNT(*) FROM users WHERE last_login_at > NOW() - INTERVAL '24 hours'"
            )
            analytics["api_calls_30d"] = await conn.fetchval(
                "SELECT COUNT(*) FROM resonant_chat_messages WHERE role = 'user' AND created_at > NOW() - INTERVAL '30 days'"
            )
            analytics["api_calls_24h"] = await conn.fetchval(
                "SELECT COUNT(*) FROM resonant_chat_messages WHERE role = 'user' AND created_at > NOW() - INTERVAL '24 hours'"
            )
        except Exception as ex:
            logger.warning(f"DB user stats failed: {ex}")
            # Fallback: try auth_service internal endpoint (no auth needed for /status)
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    auth_stats_resp = await client.get(
                        "http://auth_service:8000/owner/auth/dashboard/stats",
                        headers={"Authorization": auth_header} if auth_header else {},
                    )
                    if auth_stats_resp.status_code == 200:
                        auth_stats = auth_stats_resp.json()
                        analytics["total_users"] = auth_stats.get("total_users")
                        analytics["active_users_24h"] = auth_stats.get("active_users")
                        analytics["conversion_rate"] = auth_stats.get("conversion_rate")
            except Exception as auth_ex:
                logger.warning(f"Auth stats fallback failed: {auth_ex}")
        finally:
            if conn and pool:
                await pool.release(conn)

        async with httpx.AsyncClient(timeout=8.0) as client:
            # Get REAL billing data from billing_service
            try:
                resp = await client.get("http://billing_service:8000/billing/admin/stats")
                if resp.status_code == 200:
                    billing = resp.json()
                    analytics["revenue_30d"] = billing.get("total_revenue")
                    analytics["credits_consumed"] = billing.get("total_credits_used")
                    analytics["total_credits_purchased"] = billing.get("total_credits_purchased")
                    analytics["paying_users"] = billing.get("paying_users")
                    analytics["paid_users"] = analytics["paying_users"]
                    analytics["active_subscriptions"] = billing.get("active_subscriptions")
                    analytics["billing_status"] = "active"
            except Exception as ex:
                logger.warning(f"Billing admin stats failed: {ex}")

            # Get credit balance stats
            try:
                resp = await client.get("http://billing_service:8000/billing/credits/stats")
                if resp.status_code == 200:
                    credits_data = resp.json()
                    analytics["credits_balance"] = credits_data.get("total_balance")
                    if analytics["credits_consumed"] is None:
                        analytics["credits_consumed"] = credits_data.get("total_consumed")
            except Exception as ex:
                logger.warning(f"Credits stats failed: {ex}")

            # Get subscription stats
            try:
                resp = await client.get("http://billing_service:8000/billing/subscriptions/stats")
                if resp.status_code == 200:
                    subs = resp.json()
                    analytics["mrr"] = subs.get("mrr")
                    analytics["subscription_revenue"] = subs.get("total_revenue")
                    analytics["plan_breakdown"] = subs.get("plan_breakdown", {})
            except Exception as ex:
                logger.warning(f"Subscription stats failed: {ex}")

            # Active socket connections are still a useful live operational metric.
            try:
                analytics["active_connections"] = len(psutil.net_connections(kind="inet"))
            except Exception:
                pass

            # Calculate conversion rate
            if analytics["total_users"] and analytics["paying_users"] is not None:
                analytics["conversion_rate"] = round(
                    (analytics["paying_users"] / analytics["total_users"]) * 100, 1
                )
    except Exception as e:
        logger.error(f"Analytics error: {e}")

    analytics["timestamp"] = datetime.utcnow().isoformat()
    return analytics


# ============================================
# RECENT ACTIVITY (real events from services)
# ============================================

@router.get("/activity")
async def get_recent_activity():
    """Real recent activity from services and system events."""
    activities = []
    now_iso = datetime.utcnow().isoformat()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            # Auth service status
            try:
                resp = await client.get("http://auth_service:8000/owner/auth/status")
                if resp.status_code == 200:
                    data = resp.json()
                    activities.append({
                        "type": "auth",
                        "message": f"Auth service active - {data.get('total_users', 0)} registered users",
                        "timestamp": now_iso,
                        "category": "system",
                    })
            except Exception:
                pass

            # RARA agents
            try:
                resp = await client.get("http://rg_internal_invarients_sim:8093/agents")
                if resp.status_code == 200:
                    data = resp.json()
                    agents = data.get("agents", [])
                    activities.append({
                        "type": "rara",
                        "message": f"RARA governance: {len(agents)} agents registered, {sum(1 for a in agents if a.get('status') == 'active')} active",
                        "timestamp": now_iso,
                        "category": "agents",
                    })
                    for agent in agents[:3]:
                        aid = agent.get("id", "unknown")[:8]
                        activities.append({
                            "type": "agent_event",
                            "message": f"Agent {aid}... - type: {agent.get('type', 'unknown')}, trust: {agent.get('trust_score', 0)}, tasks: {agent.get('tasks_completed', 0)}",
                            "timestamp": now_iso,
                            "category": "agents",
                        })
            except Exception:
                pass

            # V8 engine status
            try:
                resp = await client.get(
                    "http://v8_api_service:8080/api/admin/status",
                    headers={"X-Dev-Token": V8_DEV_TOKEN},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    activities.append({
                        "type": "v8",
                        "message": f"V8 Engine v{data.get('version', '?')} - vocab: {data.get('vocab_size', 0)}, trained: {data.get('trained', False)}, forbidden: {data.get('forbidden_count', 0)}",
                        "timestamp": now_iso,
                        "category": "v8",
                    })
            except Exception:
                pass

            # Billing status
            try:
                resp = await client.get("http://billing_service:8000/api/v1/status")
                if resp.status_code == 200:
                    activities.append({
                        "type": "billing",
                        "message": "Billing service active and processing",
                        "timestamp": now_iso,
                        "category": "system",
                    })
            except Exception:
                pass

        # System uptime
        boot_time = psutil.boot_time()
        uptime = time.time() - boot_time
        activities.append({
            "type": "system",
            "message": f"System uptime: {_format_uptime(uptime)} - CPU: {psutil.cpu_percent()}%, Memory: {psutil.virtual_memory().percent}%",
            "timestamp": now_iso,
            "category": "system",
        })

    except Exception as e:
        logger.error(f"Activity fetch error: {e}")

    return {"activities": activities, "total": len(activities), "timestamp": now_iso}


# ============================================
# DATABASE STATUS
# ============================================

@router.get("/database")
async def get_database_stats():
    """Real database connection status - PostgreSQL and Redis."""
    db_stats = {
        "postgresql": {"status": "unknown"},
        "redis": {"status": "unknown"},
    }
    # Check PostgreSQL via auth_service health
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get("http://auth_service:8000/health")
            if resp.status_code == 200:
                db_stats["postgresql"] = {
                    "status": "connected",
                    "message": "PostgreSQL healthy via auth_service",
                }
            else:
                db_stats["postgresql"] = {"status": "degraded", "code": resp.status_code}
    except Exception as e:
        db_stats["postgresql"] = {"status": "offline", "error": str(e)[:100]}

    # Check Redis via TCP PING
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("shared_redis", 6379))
        s.send(b"PING\r\n")
        data = s.recv(64)
        s.close()
        if b"PONG" in data:
            db_stats["redis"] = {"status": "connected", "message": "Redis PONG OK"}
        else:
            db_stats["redis"] = {"status": "degraded"}
    except Exception as e:
        db_stats["redis"] = {"status": "offline", "error": str(e)[:100]}

    db_stats["timestamp"] = datetime.utcnow().isoformat()
    return db_stats


# ============================================
# RARA AGENTS (real data from RARA service)
# ============================================

@router.get("/rara")
async def get_rara_data():
    """Real RARA agent data with task counts, resource usage, governance."""
    rara_url = "http://rg_internal_invarients_sim:8093"
    result = {
        "agents": [],
        "total_agents": 0,
        "health": {},
        "governance": {},
        "kill_switch_active": False,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            # Get agents
            try:
                resp = await client.get(f"{rara_url}/agents")
                if resp.status_code == 200:
                    data = resp.json()
                    result["agents"] = data.get("agents", data if isinstance(data, list) else [])
                    result["total_agents"] = data.get("total", len(result["agents"]))
            except Exception:
                pass
            # Health
            try:
                resp = await client.get(f"{rara_url}/health")
                if resp.status_code == 200:
                    result["health"] = resp.json()
            except Exception:
                pass
            # Governance
            try:
                resp = await client.get(f"{rara_url}/governance/state")
                if resp.status_code == 200:
                    result["governance"] = resp.json()
            except Exception:
                pass
            # Kill switch
            try:
                resp = await client.get(f"{rara_url}/control/kill-switch/status")
                if resp.status_code == 200:
                    ks = resp.json()
                    result["kill_switch_active"] = ks.get("active", ks.get("kill_switch", False))
            except Exception:
                pass
    except Exception as e:
        logger.error(f"RARA error: {e}")
    result["timestamp"] = datetime.utcnow().isoformat()
    return result


# ============================================
# V8 ENGINE DATA (formula, status, forbidden)
# ============================================

@router.get("/v8")
async def get_v8_data():
    """Real V8 Engine status, formula parameters, forbidden words."""
    v8_data = {"status": {}, "formula": {}, "forbidden": [], "corpus": []}
    headers = {"X-Dev-Token": V8_DEV_TOKEN}
    base = "http://v8_api_service:8080"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            # Status
            try:
                resp = await client.get(f"{base}/api/admin/status", headers=headers)
                if resp.status_code == 200:
                    v8_data["status"] = resp.json()
            except Exception:
                pass
            # Formula
            try:
                resp = await client.get(f"{base}/api/admin/formula", headers=headers)
                if resp.status_code == 200:
                    v8_data["formula"] = resp.json()
            except Exception:
                pass
            # Forbidden words
            try:
                resp = await client.get(f"{base}/api/admin/forbidden", headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    v8_data["forbidden"] = data.get("words", [])
            except Exception:
                pass
            # Corpus
            try:
                resp = await client.get(f"{base}/api/admin/corpus", headers=headers)
                if resp.status_code == 200:
                    v8_data["corpus"] = resp.json()
            except Exception:
                pass
    except Exception as e:
        logger.error(f"V8 data error: {e}")
    v8_data["timestamp"] = datetime.utcnow().isoformat()
    return v8_data


# ============================================
# COMBINED OVERVIEW (for dashboard landing)
# ============================================

@router.get("/overview")
async def get_dashboard_overview(request: Request):
    """Combined overview with all key metrics for dashboard landing."""
    auth_header = request.headers.get("Authorization", "")
    overview = {}
    try:
        # System metrics
        cpu = psutil.cpu_percent(interval=0.3)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        overview["system"] = {
            "cpu_percent": round(cpu, 1),
            "memory_percent": round(mem.percent, 1),
            "disk_percent": round(disk.percent, 1),
        }
        # All services health
        tasks = [_check_service_health(k, v[0], v[1]) for k, v in SERVICE_URLS.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        healthy = sum(1 for r in results if not isinstance(r, Exception) and r.get("status") == "healthy")
        overview["services"] = {
            "total": len(SERVICE_URLS),
            "healthy": healthy,
            "offline": len(SERVICE_URLS) - healthy,
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            # RARA agents
            try:
                resp = await client.get("http://rg_internal_invarients_sim:8093/agents")
                if resp.status_code == 200:
                    data = resp.json()
                    agents = data.get("agents", [])
                    overview["agents"] = {
                        "total": data.get("total", len(agents)),
                        "active": sum(1 for a in agents if a.get("status") == "active"),
                    }
            except Exception:
                overview["agents"] = {"total": 0, "active": 0}
            # Users - query DB via pool
            ov_pool = None
            ov_conn = None
            try:
                ov_pool = await _get_db_pool()
                ov_conn = await ov_pool.acquire()
                total_u = await ov_conn.fetchval("SELECT COUNT(*) FROM users") or 0
                active_u = await ov_conn.fetchval(
                    "SELECT COUNT(*) FROM users WHERE last_login_at > NOW() - INTERVAL '24 hours'"
                ) or 0
                overview["users"] = {"total": total_u, "active_24h": active_u}
            except Exception:
                overview["users"] = {"total": 0}
            finally:
                if ov_conn and ov_pool:
                    await ov_pool.release(ov_conn)
    except Exception as e:
        logger.error(f"Overview error: {e}")
        overview["error"] = str(e)[:200]
    overview["timestamp"] = datetime.utcnow().isoformat()
    return overview
