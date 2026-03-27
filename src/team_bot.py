#!/usr/bin/env python3
"""AXIS Team Bot — standalone Telegram bot for team awareness.

Always-on bot that queries the shared Supabase event store and answers
team status questions. Also pushes proactive notifications when it
detects conflicts or significant activity from other team members.

Runs independently of Claude Code / AXIS Producer tray app.

Usage:
    python team_bot.py

Env vars:
    TELEGRAM_BOT_TOKEN  — from BotFather
    SUPABASE_URL        — your Supabase project URL
    SUPABASE_KEY        — your Supabase anon key
    ANTHROPIC_API_KEY   — for synthesis / smart queries
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

import anthropic
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from cloud_db import CloudDB

logging.basicConfig(
    format="%(asctime)s [%(name)s] %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("team_bot")

# --- Config ---

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

# Allowed Telegram user IDs (set after first /pair)
ALLOWED_USERS_FILE = os.path.join(os.path.dirname(__file__), ".team_bot_users.json")

CLAUDE_MODEL = "claude-sonnet-4-20250514"

# --- Allowed users ---


def _load_allowed() -> set[int]:
    try:
        with open(ALLOWED_USERS_FILE, "r") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def _save_allowed(users: set[int]):
    with open(ALLOWED_USERS_FILE, "w") as f:
        json.dump(list(users), f)


ALLOWED_USERS = _load_allowed()


def _is_allowed(user_id: int) -> bool:
    # If no allowlist yet, allow anyone (first-run pairing)
    if not ALLOWED_USERS:
        return True
    return user_id in ALLOWED_USERS


# --- DB ---

db: CloudDB | None = None


def get_db() -> CloudDB:
    global db
    if db is None:
        db = CloudDB(SUPABASE_URL, SUPABASE_KEY, verbose=True)
    return db


# --- Command handlers ---


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Welcome message."""
    await update.message.reply_text(
        "AXIS Team Bot\n\n"
        "Commands:\n"
        "/status — what's everyone working on\n"
        "/who <name> — what is <name> doing\n"
        "/conflicts — files touched by multiple people\n"
        "/blockers — current blockers\n"
        "/recent [minutes] — recent events (default 30)\n"
        "/synthesis — latest team synthesis\n"
        "/pair — add your Telegram ID to the allowlist\n\n"
        "Or just ask a question in natural language."
    )


async def cmd_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add the sender to the allowed users list."""
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name
    ALLOWED_USERS.add(user_id)
    _save_allowed(ALLOWED_USERS)
    await update.message.reply_text(
        f"Paired: {username} (ID: {user_id}). You're on the allowlist."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Quick overview of what everyone's doing."""
    if not _is_allowed(update.effective_user.id):
        return

    cdb = get_db()
    events = cdb.recent_events(minutes=60)

    if not events:
        await update.message.reply_text("No activity in the last hour.")
        return

    # Group by person
    by_who: dict[str, list[str]] = {}
    for e in events:
        who = e.get("who", "?")
        summary = e.get("summary", "")
        stream = e.get("stream", "")
        if who not in by_who:
            by_who[who] = []
        by_who[who].append(f"[{stream}] {summary[:80]}")

    lines = []
    for who, items in by_who.items():
        lines.append(f"*{who}* ({len(items)} events):")
        for item in items[:5]:  # cap at 5 per person
            lines.append(f"  {item}")
        if len(items) > 5:
            lines.append(f"  ...and {len(items) - 5} more")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """What is a specific person working on?"""
    if not _is_allowed(update.effective_user.id):
        return

    if not context.args:
        await update.message.reply_text("Usage: /who <name>")
        return

    name = context.args[0].lower()
    cdb = get_db()
    events = cdb.query_events(who=name, limit=20)

    if not events:
        await update.message.reply_text(f"No recent events from {name}.")
        return

    lines = [f"*{name}* — last {len(events)} events:\n"]
    for e in events:
        ts = e.get("ts", "")[:16]
        stream = e.get("stream", "")
        summary = e.get("summary", "")[:80]
        lines.append(f"`{ts}` [{stream}] {summary}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_conflicts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Files touched by multiple people recently."""
    if not _is_allowed(update.effective_user.id):
        return

    cdb = get_db()
    events = cdb.recent_events(minutes=120)

    # Collect files per person
    files_by_who: dict[str, set[str]] = {}
    for e in events:
        who = e.get("who", "?")
        files = e.get("files") or []
        if files:
            if who not in files_by_who:
                files_by_who[who] = set()
            files_by_who[who].update(files)

    if len(files_by_who) < 2:
        await update.message.reply_text("Only one person active — no conflicts possible.")
        return

    # Find overlaps
    people = list(files_by_who.keys())
    conflicts = []
    for i, p1 in enumerate(people):
        for p2 in people[i + 1:]:
            overlap = files_by_who[p1] & files_by_who[p2]
            if overlap:
                for f in overlap:
                    conflicts.append(f"⚠️ *{os.path.basename(f)}* — {p1} + {p2}")

    if not conflicts:
        await update.message.reply_text("No file conflicts detected.")
    else:
        await update.message.reply_text(
            f"*{len(conflicts)} potential conflict(s):*\n\n" + "\n".join(conflicts),
            parse_mode="Markdown",
        )


async def cmd_blockers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent blocker events."""
    if not _is_allowed(update.effective_user.id):
        return

    cdb = get_db()
    events = cdb.query_events(event_type="blocker", limit=10)

    if not events:
        # Also check voice-extracted blockers
        events = cdb.query_events(event_type="blockers", limit=10)

    if not events:
        await update.message.reply_text("No blockers logged recently.")
        return

    lines = ["*Current blockers:*\n"]
    for e in events:
        who = e.get("who", "?")
        summary = e.get("summary", "")
        lines.append(f"🔴 {who}: {summary}")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show recent events."""
    if not _is_allowed(update.effective_user.id):
        return

    minutes = 30
    if context.args:
        try:
            minutes = int(context.args[0])
        except ValueError:
            pass

    cdb = get_db()
    events = cdb.recent_events(minutes=minutes)

    if not events:
        await update.message.reply_text(f"No events in the last {minutes} minutes.")
        return

    lines = [f"*Last {minutes} min ({len(events)} events):*\n"]
    for e in events[:20]:
        ts = e.get("ts", "")
        # Extract just the time portion
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts_short = t.strftime("%H:%M")
        except (ValueError, AttributeError):
            ts_short = ts[:5]
        who = e.get("who", "?")
        stream = e.get("stream", "")
        summary = e.get("summary", "")[:60]
        lines.append(f"`{ts_short}` {who} ({stream}): {summary}")

    if len(events) > 20:
        lines.append(f"\n...and {len(events) - 20} more")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_synthesis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the latest team synthesis."""
    if not _is_allowed(update.effective_user.id):
        return

    cdb = get_db()
    synth = cdb.latest_synthesis()

    if not synth:
        await update.message.reply_text("No synthesis generated yet.")
        return

    ts = synth.get("ts", "?")
    content = synth.get("content", "")

    await update.message.reply_text(
        f"*Latest synthesis* ({ts[:16]}):\n\n{content}",
        parse_mode="Markdown",
    )


# --- Natural language queries (Claude-powered) ---

_anthropic_client: anthropic.Anthropic | None = None


def _get_anthropic() -> anthropic.Anthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.Anthropic()
    return _anthropic_client


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle free-text messages — use Claude to query the DB and answer."""
    if not _is_allowed(update.effective_user.id):
        return

    text = update.message.text
    if not text:
        return

    cdb = get_db()

    # Fetch recent context for Claude
    recent = cdb.recent_events(minutes=60)
    synth = cdb.latest_synthesis()

    event_lines = []
    for e in recent[:50]:
        who = e.get("who", "?")
        stream = e.get("stream", "?")
        summary = e.get("summary", "")
        files = e.get("files") or []
        files_str = f" [{', '.join(os.path.basename(f) for f in files)}]" if files else ""
        event_lines.append(f"[{e.get('ts', '?')[:16]}] {who} ({stream}): {summary}{files_str}")

    event_text = "\n".join(event_lines) if event_lines else "(no recent events)"
    synth_text = synth.get("content", "(none)") if synth else "(none)"

    try:
        client = _get_anthropic()
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=(
                "You are AXIS Team Bot, a concise team awareness assistant. "
                "You have access to recent team activity events and the latest synthesis. "
                "Answer the user's question based on this data. Be terse — this is a "
                "Telegram message, keep it short. Use plain text, not markdown."
            ),
            messages=[{
                "role": "user",
                "content": (
                    f"Recent events:\n{event_text}\n\n"
                    f"Latest synthesis:\n{synth_text}\n\n"
                    f"Question: {text}"
                ),
            }],
        )
        answer = resp.content[0].text if resp.content else "No answer."
    except Exception as e:
        log.error(f"Claude query failed: {e}")
        answer = f"Query failed: {e}"

    await update.message.reply_text(answer)


# --- Proactive notifications ---


class ProactiveNotifier:
    """Watches for new events and pushes notifications to paired users."""

    def __init__(self, app: Application, db: CloudDB,
                 check_interval: float = 30.0):
        self.app = app
        self.db = db
        self.check_interval = check_interval
        self._last_check = datetime.now(timezone.utc)
        self._running = False

    async def run(self):
        """Async loop — call from within the bot's event loop."""
        self._running = True
        log.info("Proactive notifier started")

        while self._running:
            await asyncio.sleep(self.check_interval)
            try:
                await self._check()
            except Exception as e:
                log.warning(f"Proactive check error: {e}")

    async def _check(self):
        """Check for notable events since last check."""
        now = datetime.now(timezone.utc)
        since = self._last_check.isoformat()
        self._last_check = now

        events = self.db.query_events(since=since, limit=50)
        if not events:
            return

        # Check for file conflicts
        files_by_who: dict[str, set[str]] = {}
        for e in events:
            who = e.get("who", "?")
            files = e.get("files") or []
            if files:
                if who not in files_by_who:
                    files_by_who[who] = set()
                files_by_who[who].update(files)

        # Detect conflicts
        people = list(files_by_who.keys())
        conflicts = []
        for i, p1 in enumerate(people):
            for p2 in people[i + 1:]:
                overlap = files_by_who[p1] & files_by_who[p2]
                for f in overlap:
                    conflicts.append(
                        f"⚠️ {p1} and {p2} both touching {os.path.basename(f)}"
                    )

        if conflicts:
            msg = "🔔 *Conflict detected:*\n\n" + "\n".join(conflicts)
            for user_id in ALLOWED_USERS:
                try:
                    await self.app.bot.send_message(
                        chat_id=user_id, text=msg, parse_mode="Markdown"
                    )
                except Exception as e:
                    log.warning(f"Failed to notify {user_id}: {e}")

        # Notify on new blockers
        blockers = [e for e in events if e.get("event_type") in ("blocker", "blockers")]
        if blockers:
            for b in blockers:
                msg = f"🔴 New blocker from {b.get('who', '?')}: {b.get('summary', '')}"
                for user_id in ALLOWED_USERS:
                    try:
                        await self.app.bot.send_message(chat_id=user_id, text=msg)
                    except Exception:
                        pass

    def stop(self):
        self._running = False


# --- Main ---


def main():
    if not BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN not set.")
        print("Create a bot via @BotFather on Telegram, then:")
        print("  set TELEGRAM_BOT_TOKEN=<your_token>")
        sys.exit(1)

    if not SUPABASE_URL or not SUPABASE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_KEY must be set.")
        sys.exit(1)

    log.info("Starting AXIS Team Bot...")

    app = Application.builder().token(BOT_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("pair", cmd_pair))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("who", cmd_who))
    app.add_handler(CommandHandler("conflicts", cmd_conflicts))
    app.add_handler(CommandHandler("blockers", cmd_blockers))
    app.add_handler(CommandHandler("recent", cmd_recent))
    app.add_handler(CommandHandler("synthesis", cmd_synthesis))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Start proactive notifier
    notifier = ProactiveNotifier(app, get_db(), check_interval=30.0)

    async def post_init(application: Application):
        asyncio.create_task(notifier.run())

    app.post_init = post_init

    log.info("Bot is running. Press Ctrl+C to stop.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
