"""Git Health Monitor — tracks push/pull staleness and branch activity.

Periodically checks:
- Unpushed commits on current branch
- Unpulled commits from remote
- Activity across all branches (including feature branches)
- Branch divergence from main

Designed to feed events into CloudSync for team visibility.
"""

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class BranchInfo:
    """Summary of a git branch's state."""
    name: str
    is_current: bool
    last_commit_time: str       # ISO 8601
    last_commit_msg: str
    last_commit_author: str
    ahead_of_main: int          # commits ahead of main/master
    behind_main: int            # commits behind main/master
    unpushed: int               # commits not pushed to remote
    unpulled: int               # commits on remote not pulled
    recent_files: list[str] = field(default_factory=list)  # files changed in last 5 commits


@dataclass
class GitHealthAlert:
    """An alert about git health."""
    alert_type: str             # "unpushed", "unpulled", "stale_branch", "divergence"
    severity: str               # "info", "warning", "critical"
    branch: str
    message: str
    details: dict = field(default_factory=dict)


def _run_git(repo_path: str, *args, timeout: int = 15) -> str:
    """Run a git command and return stdout."""
    try:
        result = subprocess.run(
            ["git"] + list(args),
            cwd=repo_path,
            capture_output=True, text=True, timeout=timeout,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return ""


class GitHealthMonitor:
    """Monitors git health and branch activity across a repo.

    callback(alert: GitHealthAlert) fires when something needs attention.
    branch_callback(branches: list[BranchInfo]) fires with full branch state.
    """

    def __init__(self, stop_event: threading.Event,
                 repo_path: str,
                 on_alert=None,
                 on_branches=None,
                 poll_interval: float = 120.0,
                 push_remind_minutes: int = 30,
                 pull_remind_minutes: int = 60,
                 verbose: bool = False):
        self.stop_event = stop_event
        self.repo_path = repo_path
        self.on_alert = on_alert
        self.on_branches = on_branches
        self.poll_interval = poll_interval
        self.push_remind_minutes = push_remind_minutes
        self.pull_remind_minutes = pull_remind_minutes
        self.verbose = verbose

        self._last_push_remind: float = 0
        self._last_pull_remind: float = 0
        self._main_branch: str = ""

    def _git(self, *args, timeout: int = 15) -> str:
        return _run_git(self.repo_path, *args, timeout=timeout)

    def _detect_main_branch(self) -> str:
        """Figure out if main branch is 'main' or 'master'."""
        if self._main_branch:
            return self._main_branch
        refs = self._git("branch", "-a")
        for name in ("main", "master"):
            if name in refs:
                self._main_branch = name
                return name
        self._main_branch = "main"
        return "main"

    def _get_branches(self) -> list[BranchInfo]:
        """Get info about all local branches."""
        main = self._detect_main_branch()
        raw = self._git("branch", "-v", "--no-abbrev")
        if not raw:
            return []

        branches = []
        for line in raw.split("\n"):
            line = line.strip()
            if not line:
                continue

            is_current = line.startswith("*")
            line = line.lstrip("* ").strip()
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue

            name = parts[0]
            commit_hash = parts[1]
            msg = parts[2] if len(parts) > 2 else ""

            # Get last commit details
            log_line = self._git("log", "-1", "--format=%aI|%an|%s", name)
            commit_time = ""
            author = ""
            if "|" in log_line:
                log_parts = log_line.split("|", 2)
                commit_time = log_parts[0]
                author = log_parts[1]
                msg = log_parts[2] if len(log_parts) > 2 else msg

            # Ahead/behind main
            ahead = 0
            behind = 0
            if name != main:
                ab = self._git("rev-list", "--left-right", "--count",
                               f"{main}...{name}")
                if ab and "\t" in ab:
                    parts_ab = ab.split("\t")
                    try:
                        behind = int(parts_ab[0])
                        ahead = int(parts_ab[1])
                    except ValueError:
                        pass

            # Unpushed/unpulled
            unpushed = 0
            unpulled = 0
            remote_ref = self._git("rev-parse", "--abbrev-ref",
                                    f"{name}@{{upstream}}")
            if remote_ref and "fatal" not in remote_ref.lower():
                up = self._git("rev-list", "--count", f"{remote_ref}..{name}")
                down = self._git("rev-list", "--count", f"{name}..{remote_ref}")
                try:
                    unpushed = int(up) if up else 0
                    unpulled = int(down) if down else 0
                except ValueError:
                    pass

            # Recent files (last 5 commits on this branch)
            files_raw = self._git("log", "-5", "--name-only",
                                   "--format=", name)
            recent_files = list(set(
                f.strip() for f in files_raw.split("\n")
                if f.strip() and not f.startswith("|")
            ))[:20]

            branches.append(BranchInfo(
                name=name,
                is_current=is_current,
                last_commit_time=commit_time,
                last_commit_msg=msg,
                last_commit_author=author,
                ahead_of_main=ahead,
                behind_main=behind,
                unpushed=unpushed,
                unpulled=unpulled,
                recent_files=recent_files,
            ))

        return branches

    def _check_health(self, branches: list[BranchInfo]) -> list[GitHealthAlert]:
        """Generate alerts from branch state."""
        alerts = []
        now = time.time()

        for b in branches:
            # Unpushed commits
            if b.unpushed > 0 and b.is_current:
                if now - self._last_push_remind > self.push_remind_minutes * 60:
                    alerts.append(GitHealthAlert(
                        alert_type="unpushed",
                        severity="warning",
                        branch=b.name,
                        message=f"{b.unpushed} unpushed commit{'s' if b.unpushed != 1 else ''} on {b.name}",
                        details={"count": b.unpushed},
                    ))
                    self._last_push_remind = now

            # Unpulled commits
            if b.unpulled > 0 and b.is_current:
                if now - self._last_pull_remind > self.pull_remind_minutes * 60:
                    alerts.append(GitHealthAlert(
                        alert_type="unpulled",
                        severity="warning",
                        branch=b.name,
                        message=f"{b.unpulled} unpulled commit{'s' if b.unpulled != 1 else ''} on {b.name} — consider pulling",
                        details={"count": b.unpulled},
                    ))
                    self._last_pull_remind = now

            # Divergence from main (feature branches only)
            if not b.is_current and b.name != self._detect_main_branch():
                if b.behind_main > 20:
                    alerts.append(GitHealthAlert(
                        alert_type="divergence",
                        severity="warning",
                        branch=b.name,
                        message=f"Branch {b.name} is {b.behind_main} commits behind {self._main_branch} — may need rebase",
                        details={"ahead": b.ahead_of_main, "behind": b.behind_main},
                    ))

        return alerts

    def _poll(self):
        """One poll cycle: fetch, analyze branches, fire callbacks."""
        # Fetch to get remote state (quiet, no merge)
        self._git("fetch", "--all", "--quiet", timeout=30)

        branches = self._get_branches()
        if not branches:
            return

        # Fire branch state callback
        if self.on_branches:
            try:
                self.on_branches(branches)
            except Exception as e:
                if self.verbose:
                    print(f"  [git-health] branch callback error: {e}")

        # Check for alerts
        alerts = self._check_health(branches)
        for alert in alerts:
            if self.verbose:
                print(f"  [git-health] {alert.severity}: {alert.message}")
            if self.on_alert:
                try:
                    self.on_alert(alert)
                except Exception as e:
                    if self.verbose:
                        print(f"  [git-health] alert callback error: {e}")

    def run(self):
        """Blocking — runs until stop_event is set."""
        if not os.path.isdir(os.path.join(self.repo_path, ".git")):
            if self.verbose:
                print(f"  [git-health] {self.repo_path} is not a git repo — disabled")
            return

        if self.verbose:
            print(f"  [git-health] monitoring {self.repo_path} "
                  f"(poll every {self.poll_interval}s)")

        while not self.stop_event.is_set():
            try:
                self._poll()
            except Exception as e:
                if self.verbose:
                    print(f"  [git-health] poll error: {e}")

            self.stop_event.wait(timeout=self.poll_interval)
