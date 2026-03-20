"""Waitlist and Referral System."""

import hashlib
import secrets
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, EmailStr


class WaitlistEntry(BaseModel):
    """Waitlist entry model."""
    email: str
    referral_code: str
    referred_by: Optional[str] = None
    position: int
    referral_count: int = 0
    status: str = "waiting"  # waiting, invited, converted
    created_at: datetime = datetime.utcnow()


class ReferralStats(BaseModel):
    """Referral statistics."""
    referral_code: str
    referral_link: str
    total_referrals: int
    successful_conversions: int
    position_boost: int
    current_position: int


# In-memory storage (replace with database in production)
waitlist_db: Dict[str, WaitlistEntry] = {}
referral_codes: Dict[str, str] = {}  # code -> email mapping


def generate_referral_code(email: str) -> str:
    """Generate unique referral code for email."""
    hash_input = f"{email}{secrets.token_hex(4)}"
    return hashlib.sha256(hash_input.encode()).hexdigest()[:8].upper()


async def add_to_waitlist(
    email: str,
    referred_by_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Add email to waitlist."""
    email = email.lower().strip()
    
    # Check if already on waitlist
    if email in waitlist_db:
        entry = waitlist_db[email]
        return {
            "status": "already_registered",
            "position": entry.position,
            "referral_code": entry.referral_code,
            "referral_link": f"https://dev-swat.com/?ref={entry.referral_code}",
        }
    
    # Generate referral code
    referral_code = generate_referral_code(email)
    
    # Calculate position
    position = len(waitlist_db) + 1
    
    # Check if referred by someone
    referred_by_email = None
    if referred_by_code and referred_by_code in referral_codes:
        referred_by_email = referral_codes[referred_by_code]
        # Boost referrer's position
        if referred_by_email in waitlist_db:
            referrer = waitlist_db[referred_by_email]
            referrer.referral_count += 1
            # Move up 10 positions per referral (min position 1)
            referrer.position = max(1, referrer.position - 10)
    
    # Create entry
    entry = WaitlistEntry(
        email=email,
        referral_code=referral_code,
        referred_by=referred_by_email,
        position=position,
    )
    
    waitlist_db[email] = entry
    referral_codes[referral_code] = email
    
    return {
        "status": "added",
        "position": position,
        "referral_code": referral_code,
        "referral_link": f"https://resonantgenesis.com/?ref={referral_code}",
        "message": f"You're #{position} on the waitlist! Share your referral link to move up.",
    }


async def get_waitlist_position(email: str) -> Optional[Dict[str, Any]]:
    """Get waitlist position for email."""
    email = email.lower().strip()
    
    if email not in waitlist_db:
        return None
    
    entry = waitlist_db[email]
    
    # Recalculate position based on referrals
    sorted_entries = sorted(
        waitlist_db.values(),
        key=lambda x: (x.position - x.referral_count * 10, x.created_at)
    )
    
    actual_position = 1
    for i, e in enumerate(sorted_entries):
        if e.email == email:
            actual_position = i + 1
            break
    
    return {
        "email": email,
        "position": actual_position,
        "referral_code": entry.referral_code,
        "referral_link": f"https://resonantgenesis.com/?ref={entry.referral_code}",
        "referral_count": entry.referral_count,
        "status": entry.status,
    }


async def get_referral_stats(email: str) -> Optional[ReferralStats]:
    """Get referral statistics for user."""
    email = email.lower().strip()
    
    if email not in waitlist_db:
        return None
    
    entry = waitlist_db[email]
    position_info = await get_waitlist_position(email)
    
    return ReferralStats(
        referral_code=entry.referral_code,
        referral_link=f"https://resonantgenesis.com/?ref={entry.referral_code}",
        total_referrals=entry.referral_count,
        successful_conversions=0,  # Track when referrals convert to paid
        position_boost=entry.referral_count * 10,
        current_position=position_info["position"] if position_info else entry.position,
    )


async def get_waitlist_count() -> int:
    """Get total waitlist count."""
    return len(waitlist_db)


async def invite_from_waitlist(count: int = 100) -> List[str]:
    """Invite top N users from waitlist."""
    sorted_entries = sorted(
        [e for e in waitlist_db.values() if e.status == "waiting"],
        key=lambda x: (x.position - x.referral_count * 10, x.created_at)
    )
    
    invited = []
    for entry in sorted_entries[:count]:
        entry.status = "invited"
        invited.append(entry.email)
    
    return invited


async def mark_converted(email: str) -> bool:
    """Mark user as converted (signed up)."""
    email = email.lower().strip()
    
    if email not in waitlist_db:
        return False
    
    waitlist_db[email].status = "converted"
    return True


# API Router
from fastapi import APIRouter, HTTPException

waitlist_router = APIRouter(prefix="/waitlist", tags=["waitlist"])


class WaitlistRequest(BaseModel):
    email: EmailStr
    referral_code: Optional[str] = None


class PositionRequest(BaseModel):
    email: EmailStr


@waitlist_router.post("")
async def join_waitlist(request: WaitlistRequest):
    """Join the waitlist."""
    result = await add_to_waitlist(
        email=request.email,
        referred_by_code=request.referral_code,
    )
    return result


@waitlist_router.get("/position/{email}")
async def check_position(email: str):
    """Check waitlist position."""
    result = await get_waitlist_position(email)
    if not result:
        raise HTTPException(status_code=404, detail="Email not found on waitlist")
    return result


@waitlist_router.get("/stats/{email}")
async def get_stats(email: str):
    """Get referral statistics."""
    result = await get_referral_stats(email)
    if not result:
        raise HTTPException(status_code=404, detail="Email not found on waitlist")
    return result


@waitlist_router.get("/count")
async def waitlist_count():
    """Get total waitlist count."""
    count = await get_waitlist_count()
    return {"count": count}
