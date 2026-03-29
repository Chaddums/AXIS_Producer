"""Supabase client and RLS-scoped helpers."""

from supabase import create_client, Client

_client: Client | None = None


def init(url: str, service_key: str):
    global _client
    _client = create_client(url, service_key)


def client() -> Client:
    if _client is None:
        raise RuntimeError("db.init() not called")
    return _client


def scoped_client(team_id: str) -> Client:
    """Return a client with RLS headers set for a specific team."""
    c = client()
    c.postgrest.auth(token=None, headers={"x-team-id": team_id})
    return c


# --- User operations ---

def create_user(email: str, password_hash: str, name: str) -> dict | None:
    res = client().table("users").insert({
        "email": email,
        "password_hash": password_hash,
        "name": name,
    }).execute()
    return res.data[0] if res.data else None


def get_user_by_email(email: str) -> dict | None:
    res = client().table("users").select("*").eq("email", email).execute()
    return res.data[0] if res.data else None


def get_user_by_id(user_id: str) -> dict | None:
    res = client().table("users").select("*").eq("id", user_id).execute()
    return res.data[0] if res.data else None


# --- Team operations ---

def create_team(name: str, owner_id: str) -> dict | None:
    res = client().table("teams").insert({
        "name": name,
        "owner_id": owner_id,
    }).execute()
    return res.data[0] if res.data else None


def get_team(team_id: str) -> dict | None:
    res = client().table("teams").select("*").eq("id", team_id).execute()
    return res.data[0] if res.data else None


def add_team_member(team_id: str, user_id: str, role: str = "member") -> dict | None:
    try:
        res = client().table("team_members").insert({
            "team_id": team_id,
            "user_id": user_id,
            "role": role,
        }).execute()
        return res.data[0] if res.data else None
    except Exception as e:
        if "duplicate" in str(e).lower() or "unique" in str(e).lower() or "23505" in str(e):
            return {"team_id": team_id, "user_id": user_id, "role": role, "already_member": True}
        raise


def get_team_members(team_id: str) -> list[dict]:
    res = client().table("team_members").select("*, users(id, name, email)").eq("team_id", team_id).execute()
    return res.data or []


def get_user_teams(user_id: str) -> list[dict]:
    res = client().table("team_members").select("*, teams(*)").eq("user_id", user_id).execute()
    return res.data or []


# --- Invite operations ---

def create_invite(team_id: str, code: str, created_by: str) -> dict | None:
    res = client().table("invites").insert({
        "team_id": team_id,
        "code": code,
        "created_by": created_by,
    }).execute()
    return res.data[0] if res.data else None


def get_invite(code: str) -> dict | None:
    res = client().table("invites").select("*").eq("code", code).eq("used", False).execute()
    return res.data[0] if res.data else None


def mark_invite_used(invite_id: str, used_by: str):
    client().table("invites").update({
        "used": True,
        "used_by": used_by,
    }).eq("id", invite_id).execute()


# --- Usage tracking ---

def get_monthly_usage(team_id: str, year_month: str) -> float:
    """Get total API cost for a team in a given month (YYYY-MM)."""
    res = client().table("usage").select("cost_usd").eq(
        "team_id", team_id
    ).gte("created_at", f"{year_month}-01").lt(
        "created_at", f"{year_month}-32"
    ).execute()
    return sum(row["cost_usd"] for row in (res.data or []))


def record_usage(team_id: str, user_id: str, service: str,
                 tokens_in: int, tokens_out: int, cost_usd: float):
    client().table("usage").insert({
        "team_id": team_id,
        "user_id": user_id,
        "service": service,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
    }).execute()


# --- Subscription operations ---

def create_subscription(team_id: str, stripe_customer_id: str,
                        stripe_subscription_id: str, tier: str,
                        status: str, current_period_end: int | None = None,
                        seats: int = 1) -> dict | None:
    from datetime import datetime, timezone
    period_end = None
    if current_period_end:
        period_end = datetime.fromtimestamp(current_period_end, tz=timezone.utc).isoformat()
    res = client().table("subscriptions").insert({
        "team_id": team_id,
        "stripe_customer_id": stripe_customer_id,
        "stripe_subscription_id": stripe_subscription_id,
        "tier": tier,
        "status": status,
        "current_period_end": period_end,
        "seats": seats,
    }).execute()
    return res.data[0] if res.data else None


def get_subscription_by_team(team_id: str) -> dict | None:
    res = client().table("subscriptions").select("*").eq(
        "team_id", team_id
    ).order("created_at", desc=True).limit(1).execute()
    return res.data[0] if res.data else None


def get_subscription_by_stripe_id(stripe_subscription_id: str) -> dict | None:
    res = client().table("subscriptions").select("*").eq(
        "stripe_subscription_id", stripe_subscription_id
    ).execute()
    return res.data[0] if res.data else None


def update_subscription(stripe_subscription_id: str, status: str,
                        current_period_end: int | None = None):
    from datetime import datetime, timezone
    updates = {"status": status, "updated_at": datetime.now(timezone.utc).isoformat()}
    if current_period_end:
        updates["current_period_end"] = datetime.fromtimestamp(
            current_period_end, tz=timezone.utc
        ).isoformat()
    client().table("subscriptions").update(updates).eq(
        "stripe_subscription_id", stripe_subscription_id
    ).execute()


def set_user_stripe_customer(user_id: str, stripe_customer_id: str):
    client().table("users").update({
        "stripe_customer_id": stripe_customer_id,
    }).eq("id", user_id).execute()


# --- Email verification ---

def create_email_verification(user_id: str, token: str, expires_at: str) -> dict | None:
    res = client().table("email_verifications").insert({
        "user_id": user_id,
        "token": token,
        "expires_at": expires_at,
    }).execute()
    return res.data[0] if res.data else None


def get_email_verification(token: str) -> dict | None:
    res = client().table("email_verifications").select("*").eq(
        "token", token
    ).eq("used", False).execute()
    return res.data[0] if res.data else None


def mark_email_verified(user_id: str, verification_id: str):
    client().table("users").update({"email_verified": True}).eq("id", user_id).execute()
    client().table("email_verifications").update({"used": True}).eq(
        "id", verification_id
    ).execute()


# --- Password reset ---

def create_password_reset(user_id: str, token: str, expires_at: str) -> dict | None:
    res = client().table("password_resets").insert({
        "user_id": user_id,
        "token": token,
        "expires_at": expires_at,
    }).execute()
    return res.data[0] if res.data else None


def get_password_reset(token: str) -> dict | None:
    res = client().table("password_resets").select("*").eq(
        "token", token
    ).eq("used", False).execute()
    return res.data[0] if res.data else None


def use_password_reset(reset_id: str, user_id: str, new_password_hash: str):
    client().table("password_resets").update({"used": True}).eq("id", reset_id).execute()
    client().table("users").update({"password_hash": new_password_hash}).eq(
        "id", user_id
    ).execute()


# --- Attestation / flagging ---

def create_attestation(user_id: str, ip_address: str | None = None) -> dict | None:
    res = client().table("attestations").insert({
        "user_id": user_id,
        "ip_address": ip_address,
    }).execute()
    return res.data[0] if res.data else None


def create_flagged_account(user_id: str, reason: str,
                           matched_keywords: list[str]) -> dict | None:
    res = client().table("flagged_accounts").insert({
        "user_id": user_id,
        "reason": reason,
        "matched_keywords": matched_keywords,
    }).execute()
    return res.data[0] if res.data else None


# --- Team config ---

def update_team_config(team_id: str, **kwargs) -> dict | None:
    allowed = {"workspace_type", "workspace_context", "output_terminology", "privacy_preset"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return None
    res = client().table("teams").update(updates).eq("id", team_id).execute()
    return res.data[0] if res.data else None


# --- Event status ---

def get_event_by_id(event_id: int) -> dict | None:
    res = client().table("events").select("*").eq("id", event_id).execute()
    return res.data[0] if res.data else None


def update_event_status(event_id: int, status: str, who: str) -> dict | None:
    from datetime import datetime, timezone
    updates = {"status": status, "resolved_by": who}
    if status in ("resolved", "dismissed"):
        updates["resolved_at"] = datetime.now(timezone.utc).isoformat()
    res = client().table("events").update(updates).eq("id", event_id).execute()
    return res.data[0] if res.data else None


# --- Syntheses ---

def insert_synthesis(team_id: str, content: str,
                     window_start: str, window_end: str) -> dict | None:
    res = client().table("syntheses").insert({
        "team_id": team_id,
        "content": content,
        "window_start": window_start,
        "window_end": window_end,
    }).execute()
    return res.data[0] if res.data else None


def get_latest_synthesis(team_id: str) -> dict | None:
    res = client().table("syntheses").select("*").eq(
        "team_id", team_id
    ).order("created_at", desc=True).limit(1).execute()
    return res.data[0] if res.data else None
