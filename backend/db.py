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
    res = client().table("team_members").insert({
        "team_id": team_id,
        "user_id": user_id,
        "role": role,
    }).execute()
    return res.data[0] if res.data else None


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
