"""Slack Monitor — polls Slack channels for new messages via Web API.

Requires a Slack Bot Token (xoxb-...) with channels:history + channels:read scopes.
Set SLACK_BOT_TOKEN env var, or configure in tray_settings.json.

Polls watched channels every N seconds for new messages since last check.
Pushes each new message to the focus advisor for priority matching.
"""

import os
import threading
import time
from datetime import datetime

try:
    import urllib.request
    import json as _json
    _HAS_REQUESTS = False
except ImportError:
    _HAS_REQUESTS = False


def _slack_api(method: str, token: str, params: dict = None) -> dict:
    """Call Slack Web API using urllib (no extra deps)."""
    url = f"https://slack.com/api/{method}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url = f"{url}?{query}"

    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")

    with urllib.request.urlopen(req, timeout=10) as resp:
        return _json.loads(resp.read().decode())


class SlackMonitor:
    """Polls Slack channels for new messages and forwards to a callback.

    callback(source: str, sender: str, text: str, timestamp: str)
    """

    def __init__(self, stop_event: threading.Event,
                 on_message=None,
                 token: str | None = None,
                 channel_ids: list[str] | None = None,
                 poll_interval: float = 15.0,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.on_message = on_message
        self.token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        self.channel_ids = channel_ids or []
        self.poll_interval = poll_interval
        self.verbose = verbose

        # Track last-seen timestamp per channel
        self._last_ts: dict[str, str] = {}
        # Cache channel id → name
        self._channel_names: dict[str, str] = {}

    def _resolve_channel_names(self):
        """Fetch channel names for display."""
        if not self.token:
            return
        try:
            resp = _slack_api("conversations.list", self.token,
                              {"types": "public_channel,private_channel", "limit": "200"})
            if resp.get("ok"):
                for ch in resp.get("channels", []):
                    self._channel_names[ch["id"]] = ch["name"]
        except Exception as e:
            if self.verbose:
                print(f"  [slack] channel list error: {e}")

    def _discover_channels(self):
        """If no channels configured, watch all channels the bot is in."""
        if self.channel_ids:
            return
        if not self.token:
            return
        try:
            resp = _slack_api("conversations.list", self.token,
                              {"types": "public_channel,private_channel",
                               "limit": "100"})
            if resp.get("ok"):
                for ch in resp.get("channels", []):
                    if ch.get("is_member"):
                        self.channel_ids.append(ch["id"])
                        self._channel_names[ch["id"]] = ch["name"]
                if self.verbose:
                    names = [self._channel_names.get(c, c) for c in self.channel_ids]
                    print(f"  [slack] watching channels: {', '.join(names)}")
        except Exception as e:
            if self.verbose:
                print(f"  [slack] discover error: {e}")

    def _poll_channel(self, channel_id: str):
        """Fetch new messages from a channel since last check."""
        params = {"channel": channel_id, "limit": "20"}
        oldest = self._last_ts.get(channel_id)
        if oldest:
            params["oldest"] = oldest

        try:
            resp = _slack_api("conversations.history", self.token, params)
        except Exception as e:
            if self.verbose:
                print(f"  [slack] poll error on {channel_id}: {e}")
            return

        if not resp.get("ok"):
            if self.verbose:
                print(f"  [slack] API error: {resp.get('error', 'unknown')}")
            return

        messages = resp.get("messages", [])
        if not messages:
            return

        # Update last-seen timestamp (messages come newest-first)
        self._last_ts[channel_id] = messages[0]["ts"]

        # Skip if this is the first poll (don't flood with history)
        if oldest is None:
            return

        channel_name = self._channel_names.get(channel_id, channel_id)

        for msg in reversed(messages):  # oldest first
            text = msg.get("text", "").strip()
            if not text:
                continue
            # Skip bot messages and system messages
            if msg.get("subtype") in ("bot_message", "channel_join",
                                       "channel_leave", "channel_topic"):
                continue

            sender = msg.get("user", "unknown")
            ts = datetime.fromtimestamp(float(msg["ts"])).strftime("%H:%M")

            if self.verbose:
                preview = text[:60] + ("..." if len(text) > 60 else "")
                print(f"  [slack] #{channel_name} {ts}: {preview}")

            if self.on_message:
                self.on_message(
                    source=f"slack:#{channel_name}",
                    sender=sender,
                    text=text,
                    timestamp=ts,
                )

    def run(self):
        """Blocking — runs until stop_event is set."""
        if not self.token:
            if self.verbose:
                print("  [slack] no token — monitor disabled "
                      "(set SLACK_BOT_TOKEN or configure in settings)")
            return

        self._resolve_channel_names()
        self._discover_channels()

        if not self.channel_ids:
            if self.verbose:
                print("  [slack] no channels to watch — monitor disabled")
            return

        if self.verbose:
            print(f"  [slack] monitoring {len(self.channel_ids)} channels "
                  f"every {self.poll_interval}s")

        while not self.stop_event.is_set():
            for ch_id in self.channel_ids:
                if self.stop_event.is_set():
                    break
                self._poll_channel(ch_id)

            self.stop_event.wait(timeout=self.poll_interval)
