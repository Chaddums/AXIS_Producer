"""Prohibited use detection — domain blocklist, keyword scanning, attestation."""

from datetime import datetime, timezone

import db

# Email domains that are silently blocked at signup.
# Generic error returned — never explain why.
BLOCKED_DOMAINS = {
    ".gov", ".mil",
    ".dhs.gov", ".ice.dhs.gov", ".cbp.dhs.gov",
    ".fbi.gov", ".dea.gov", ".atf.gov",
    ".usms.gov", ".bop.gov",
    ".ic.gov", ".nsa.gov", ".cia.gov",
    ".state.gov", ".doj.gov", ".dod.mil",
}

# Organization name keywords that trigger manual review (not blocking).
FLAG_KEYWORDS = [
    "police", "sheriff", "federal", "government", "enforcement",
    "homeland", "immigration", "detention", "surveillance",
    "ice", "fbi", "dea", "atf", "cbp", "dhs", "nsa", "cia",
    "doj", "dod", "usms", "bop",
    "department of", "bureau of", "office of",
    "task force", "fusion center",
    "corrections", "probation", "parole",
    "marshal", "customs", "border",
]


def check_email_domain(email: str) -> bool:
    """Return True if the email domain is blocked."""
    domain = email.lower().split("@")[-1]
    for blocked in BLOCKED_DOMAINS:
        if domain.endswith(blocked.lstrip(".")):
            return True
    return False


def scan_org_name(name: str) -> list[str]:
    """Return list of matched flag keywords in the organization name."""
    lower = name.lower()
    return [kw for kw in FLAG_KEYWORDS if kw in lower]


def log_attestation(user_id: str, ip_address: str | None = None):
    """Record self-attestation. Legal artifact — never delete."""
    db.create_attestation(user_id, ip_address)


def flag_account(user_id: str, reason: str, matched_keywords: list[str]):
    """Flag an account for manual review."""
    db.create_flagged_account(user_id, reason, matched_keywords)
