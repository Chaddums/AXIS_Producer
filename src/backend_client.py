"""Backend API client — desktop-side HTTP wrapper for all backend calls.

Replaces direct Supabase access. All cloud operations go through the backend.
Falls back gracefully when backend is unreachable (local-only mode).
"""

import logging

import httpx

log = logging.getLogger(__name__)


class BackendClient:
    """Synchronous HTTP client wrapping the AXIS backend API."""

    def __init__(self, base_url: str, token: str = "", verbose: bool = False):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.verbose = verbose
        self._http = httpx.Client(timeout=30.0)

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = f"Bearer {self.token}"
        return h

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _request(self, method: str, path: str, **kwargs) -> dict | None:
        try:
            resp = self._http.request(method, self._url(path),
                                       headers=self._headers(), **kwargs)
            if resp.status_code >= 400:
                if self.verbose:
                    log.warning(f"Backend {method} {path}: {resp.status_code} {resp.text[:200]}")
                return {"_error": True, "_status": resp.status_code, "_detail": resp.text}
            return resp.json()
        except Exception as e:
            if self.verbose:
                log.warning(f"Backend {method} {path} failed: {e}")
            return None

    @property
    def connected(self) -> bool:
        try:
            resp = self._http.get(self._url("/health"), timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    # --- Auth ---

    def signup(self, email: str, password: str, name: str,
               organization: str = "", self_attested: bool = True) -> dict | None:
        return self._request("POST", "/auth/signup", json={
            "email": email, "password": password, "name": name,
            "organization": organization, "self_attested": self_attested,
        })

    def login(self, email: str, password: str) -> dict | None:
        return self._request("POST", "/auth/login", json={
            "email": email, "password": password,
        })

    def refresh_token(self) -> dict | None:
        return self._request("POST", "/auth/refresh", json={
            "token": self.token,
        })

    def verify_email(self, token: str) -> dict | None:
        return self._request("POST", "/auth/verify-email", json={"token": token})

    def forgot_password(self, email: str) -> dict | None:
        return self._request("POST", "/auth/forgot-password", json={"email": email})

    def reset_password(self, token: str, new_password: str) -> dict | None:
        return self._request("POST", "/auth/reset-password", json={
            "token": token, "new_password": new_password,
        })

    # --- Teams ---

    def create_team(self, name: str) -> dict | None:
        return self._request("POST", "/teams", json={"name": name})

    def list_teams(self) -> list[dict]:
        result = self._request("GET", "/teams")
        return result if isinstance(result, list) else []

    def create_invite(self, team_id: str) -> dict | None:
        return self._request("POST", f"/teams/{team_id}/invite")

    def join_team(self, code: str) -> dict | None:
        return self._request("POST", "/teams/join", json={"code": code})

    def list_members(self, team_id: str) -> list[dict]:
        result = self._request("GET", f"/teams/{team_id}/members")
        return result if isinstance(result, list) else []

    def update_team_config(self, team_id: str, **kwargs) -> dict | None:
        return self._request("PUT", f"/teams/{team_id}/config", json=kwargs)

    # --- Events (replaces CloudDB) ---

    def push_events(self, events: list[dict]) -> int:
        """Push a batch of events. Returns count of inserted."""
        if not events:
            return 0
        result = self._request("POST", "/events/batch", json=events)
        if result and not result.get("_error"):
            return result.get("count", 0)
        # Fallback: try one at a time
        count = 0
        for event in events:
            r = self._request("POST", "/events", json=event)
            if r and not r.get("_error"):
                count += 1
        return count

    def poll_events(self, team_id: str, since: str | None = None,
                    since_id: str | None = None, limit: int = 100) -> list[dict]:
        params = {"team_id": team_id, "limit": str(limit)}
        if since_id:
            params["since_id"] = since_id
        elif since:
            params["since"] = since
        result = self._request("GET", "/events", params=params)
        return result if isinstance(result, list) else []

    def push_synthesis(self, team_id: str, content: str,
                       window_start: str, window_end: str) -> dict | None:
        return self._request("POST", "/events/synthesis", json={
            "team_id": team_id, "content": content,
            "window_start": window_start, "window_end": window_end,
        })

    def get_latest_synthesis(self, team_id: str) -> dict | None:
        return self._request("GET", "/events/synthesis/latest",
                             params={"team_id": team_id})

    # --- Proxy (replaces direct Anthropic/Groq calls) ---

    def claude_batch(self, team_id: str, system: str, transcript: str,
                     model: str = "claude-sonnet-4-20250514",
                     max_tokens: int = 1024) -> dict | None:
        """Send a transcript batch through the metered backend proxy."""
        return self._request("POST", "/proxy/anthropic/batch", json={
            "team_id": team_id, "system": system, "transcript": transcript,
            "model": model, "max_tokens": max_tokens,
        })

    def groq_transcribe(self, team_id: str, audio_bytes: bytes,
                        filename: str = "audio.wav") -> dict | None:
        """Transcribe audio via the backend Groq proxy."""
        try:
            resp = self._http.post(
                self._url("/proxy/groq/transcribe"),
                headers={"Authorization": f"Bearer {self.token}"},
                files={"audio": (filename, audio_bytes, "audio/wav")},
                data={"team_id": team_id},
            )
            if resp.status_code >= 400:
                return None
            return resp.json()
        except Exception as e:
            if self.verbose:
                log.warning(f"Groq transcribe failed: {e}")
            return None

    # --- Billing ---

    def create_checkout(self, tier: str, team_id: str,
                        promo_code: str | None = None,
                        seats: int = 1) -> dict | None:
        payload = {"tier": tier, "team_id": team_id, "seats": seats}
        if promo_code:
            payload["promo_code"] = promo_code
        return self._request("POST", "/billing/checkout", json=payload)

    def get_subscription_status(self, team_id: str) -> dict | None:
        return self._request("GET", "/billing/status", params={"team_id": team_id})

    def create_billing_portal(self, team_id: str) -> dict | None:
        return self._request("POST", "/billing/portal", params={"team_id": team_id})

    # --- Lifecycle ---

    def close(self):
        self._http.close()
