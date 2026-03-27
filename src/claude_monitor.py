"""Claude Code Conversation Monitor — tails JSONL session files for activity.

Watches ~/.claude/projects/ for Claude Code conversation files. Extracts
structured events from user messages, tool calls, and file edits.

Follows the same monitor pattern as SlackMonitor/VcsMonitor:
    __init__(stop_event, callback, ...), blocking run(), graceful shutdown.
"""

import glob
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ClaudeEvent:
    """A single event extracted from a Claude Code conversation."""
    timestamp: str          # ISO 8601
    session_id: str
    event_type: str         # user_message, file_edit, file_read, bash_command, search, write
    summary: str            # human-readable one-liner
    files: list[str] = field(default_factory=list)
    tool_name: str = ""     # Edit, Read, Bash, Grep, Glob, Write, etc.
    branch: str = ""
    project: str = ""
    raw: dict = field(default_factory=dict, repr=False)


# Map tool names to event types
_TOOL_EVENT_TYPES = {
    "Edit": "file_edit",
    "Write": "write",
    "Read": "file_read",
    "Bash": "bash_command",
    "Grep": "search",
    "Glob": "search",
    "Agent": "agent_dispatch",
}


def _extract_files_from_input(tool_name: str, tool_input: dict) -> list[str]:
    """Pull file paths from a tool_use input dict."""
    files = []
    for key in ("file_path", "path", "file"):
        val = tool_input.get(key)
        if val and isinstance(val, str):
            files.append(val)
    # For Glob, the pattern itself is useful context
    if tool_name == "Glob" and "pattern" in tool_input:
        pass  # pattern isn't a file path
    return files


def _summarize_tool_use(tool_name: str, tool_input: dict) -> str:
    """Generate a short summary for a tool call."""
    if tool_name == "Edit":
        fp = tool_input.get("file_path", "?")
        return f"Edit {os.path.basename(fp)}"
    elif tool_name == "Write":
        fp = tool_input.get("file_path", "?")
        return f"Write {os.path.basename(fp)}"
    elif tool_name == "Read":
        fp = tool_input.get("file_path", "?")
        return f"Read {os.path.basename(fp)}"
    elif tool_name == "Bash":
        cmd = tool_input.get("command", "?")
        return f"Bash: {cmd[:80]}"
    elif tool_name == "Grep":
        pat = tool_input.get("pattern", "?")
        return f"Grep: {pat[:60]}"
    elif tool_name == "Glob":
        pat = tool_input.get("pattern", "?")
        return f"Glob: {pat[:60]}"
    elif tool_name == "Agent":
        desc = tool_input.get("description", tool_input.get("prompt", "?")[:60])
        return f"Agent: {desc[:60]}"
    else:
        return f"{tool_name}"


def _project_name_from_dir(dirname: str) -> str:
    """Extract a human-friendly project name from the Claude projects directory name.

    e.g. 'C--Users-Stu-GitHub-Crawler-Project-Godot-TD' -> 'Godot-TD'
    """
    # Split on '--' (drive separator) then '-' segments, take the last meaningful part
    parts = dirname.replace("--", "/").split("-")
    # Find the last non-trivial segment
    # The directory name encodes the full path with - as separator
    # Just take the last 1-2 segments for a readable name
    if len(parts) >= 2:
        return "-".join(parts[-2:])
    return parts[-1] if parts else dirname


class ClaudeMonitor:
    """Polls Claude Code JSONL conversation files for new events.

    callback(event: ClaudeEvent) fires for each meaningful new line.
    """

    def __init__(self, stop_event: threading.Event,
                 on_event=None,
                 project_paths: list[str] | None = None,
                 poll_interval: float = 3.0,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.on_event = on_event
        self.poll_interval = poll_interval
        self.verbose = verbose

        # Which project dirs under ~/.claude/projects/ to watch
        # Empty list = watch nothing (must explicitly opt in projects)
        # Use ["*"] to watch all projects
        self._project_filters = project_paths or []

        # Track byte offsets per file for tailing
        self._file_positions: dict[str, int] = {}

        # Dedup by message uuid
        self._seen_uuids: set[str] = set()
        self._MAX_SEEN = 2000

        # Base path for Claude Code projects
        self._base_path = os.path.join(Path.home(), ".claude", "projects")

    def _discover_jsonl_files(self) -> list[str]:
        """Find all JSONL conversation files in watched project directories."""
        if not os.path.isdir(self._base_path):
            return []

        files = []
        try:
            for entry in os.listdir(self._base_path):
                proj_dir = os.path.join(self._base_path, entry)
                if not os.path.isdir(proj_dir):
                    continue

                # Only watch explicitly opted-in projects
                # Use ["*"] to watch all, otherwise must match a filter
                if not self._project_filters:
                    continue  # no filters = watch nothing
                if "*" not in self._project_filters:
                    if not any(f.lower() in entry.lower() for f in self._project_filters):
                        continue

                # Find .jsonl files directly in the project dir (not in subdirs)
                for f in glob.glob(os.path.join(proj_dir, "*.jsonl")):
                    files.append(f)
        except OSError:
            pass

        return files

    def _init_file(self, filepath: str):
        """Register a new file — seek to end so we only tail new content."""
        try:
            size = os.path.getsize(filepath)
            self._file_positions[filepath] = size
            if self.verbose:
                print(f"  [claude] tracking: {os.path.basename(filepath)} "
                      f"(skipping {size} bytes of history)")
        except OSError:
            self._file_positions[filepath] = 0

    def _tail_file(self, filepath: str) -> list[str]:
        """Read new lines appended since last check."""
        try:
            size = os.path.getsize(filepath)
        except OSError:
            return []

        offset = self._file_positions.get(filepath, size)
        if size <= offset:
            return []

        lines = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                lines = f.readlines()
            self._file_positions[filepath] = size
        except OSError:
            pass

        return lines

    def _parse_line(self, line: str, project: str) -> list[ClaudeEvent]:
        """Parse a single JSONL line into zero or more ClaudeEvents."""
        line = line.strip()
        if not line:
            return []

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []

        msg_type = data.get("type", "")
        uuid = data.get("uuid", "")

        # Dedup
        if uuid:
            if uuid in self._seen_uuids:
                return []
            self._seen_uuids.add(uuid)
            if len(self._seen_uuids) > self._MAX_SEEN:
                # Prune half
                to_remove = list(self._seen_uuids)[:self._MAX_SEEN // 2]
                for u in to_remove:
                    self._seen_uuids.discard(u)

        timestamp = data.get("timestamp", "")
        session_id = data.get("sessionId", "")
        branch = data.get("gitBranch", "")

        events = []

        if msg_type == "user":
            message = data.get("message", {})
            if not isinstance(message, dict):
                return []
            content = message.get("content", "")

            # Check if this is a tool result (has toolUseResult)
            tool_result = data.get("toolUseResult")
            if tool_result and isinstance(tool_result, dict) and tool_result.get("filePath"):
                events.append(ClaudeEvent(
                    timestamp=timestamp,
                    session_id=session_id,
                    event_type="file_edit",
                    summary=f"Applied edit to {os.path.basename(tool_result['filePath'])}",
                    files=[tool_result["filePath"]],
                    tool_name="Edit",
                    branch=branch,
                    project=project,
                    raw=data,
                ))
            elif isinstance(content, str) and content.strip():
                # User typed a message — this is intent
                events.append(ClaudeEvent(
                    timestamp=timestamp,
                    session_id=session_id,
                    event_type="user_message",
                    summary=content[:120].replace("\n", " "),
                    files=[],
                    tool_name="",
                    branch=branch,
                    project=project,
                    raw=data,
                ))

        elif msg_type == "assistant":
            message = data.get("message", {})
            content_blocks = message.get("content", [])
            if not isinstance(content_blocks, list):
                return []

            for block in content_blocks:
                if not isinstance(block, dict):
                    continue
                if block.get("type") != "tool_use":
                    continue

                tool_name = block.get("name", "")
                tool_input = block.get("input", {})

                # Skip noisy/meta tools
                if tool_name in ("TaskCreate", "TaskUpdate", "TaskGet",
                                 "TaskList", "TaskStop", "TaskOutput",
                                 "ToolSearch", "Skill"):
                    continue

                files = _extract_files_from_input(tool_name, tool_input)
                event_type = _TOOL_EVENT_TYPES.get(tool_name, "tool_use")
                summary = _summarize_tool_use(tool_name, tool_input)

                events.append(ClaudeEvent(
                    timestamp=timestamp,
                    session_id=session_id,
                    event_type=event_type,
                    summary=summary,
                    files=files,
                    tool_name=tool_name,
                    branch=branch,
                    project=project,
                    raw=data,
                ))

        # Skip: progress, system, file-history-snapshot
        return events

    def _poll(self):
        """One poll cycle: discover files, tail new lines, parse and emit."""
        current_files = set(self._discover_jsonl_files())

        # Register new files
        for f in current_files:
            if f not in self._file_positions:
                self._init_file(f)

        # Tail all tracked files
        for filepath in list(self._file_positions.keys()):
            # Derive project name from the parent directory
            proj_dir_name = os.path.basename(os.path.dirname(filepath))
            # Skip subdirectories (subagent files live in session UUID dirs)
            if not filepath.endswith(".jsonl"):
                continue

            project = _project_name_from_dir(proj_dir_name)
            new_lines = self._tail_file(filepath)

            for line in new_lines:
                events = self._parse_line(line, project)
                for event in events:
                    if self.verbose:
                        print(f"  [claude] {event.event_type}: {event.summary}")
                    if self.on_event:
                        try:
                            self.on_event(event)
                        except Exception as e:
                            if self.verbose:
                                print(f"  [claude] callback error: {e}")

    def run(self):
        """Blocking — runs until stop_event is set."""
        if not os.path.isdir(self._base_path):
            if self.verbose:
                print(f"  [claude] {self._base_path} not found — monitor disabled")
            return

        if self.verbose:
            filters = self._project_filters or ["all projects"]
            print(f"  [claude] monitoring: {', '.join(filters)} "
                  f"(poll every {self.poll_interval}s)")

        while not self.stop_event.is_set():
            try:
                self._poll()
            except Exception as e:
                if self.verbose:
                    print(f"  [claude] poll error: {e}")

            self.stop_event.wait(timeout=self.poll_interval)
