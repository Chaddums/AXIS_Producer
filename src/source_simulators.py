"""Source Simulators — generate realistic events from different enterprise platforms.

Pulls real data from public sources and pushes events tagged with the correct
stream type. Used for load testing and demo purposes.

Sources:
    - git: Real commits from public GitHub repos
    - slack: Synthetic threaded conversations
    - p4v: Simulated Perforce changelists (from Git data)
    - zoom/teams: Meeting transcripts (from YouTube captions)
    - email: Synthetic email digests

Usage:
    from source_simulators import GitSimulator, SlackSimulator, ...
    sim = GitSimulator(backend, team_id, "Alex Chen")
    sim.run()  # pushes events
"""

import json
import random
import time
import threading
from datetime import datetime, timezone, timedelta

import httpx


# Realistic Slack message templates
SLACK_TEMPLATES = {
    "standup": [
        "Yesterday: {task1}. Today: {task2}. No blockers.",
        "Done with {task1}, moving to {task2}. Might need help with {blocker}.",
        "Wrapped up {task1}. Starting {task2} today. {blocker} is still pending.",
        "Finished {task1}. Today focusing on {task2}.",
    ],
    "question": [
        "Has anyone looked at {topic} recently? I'm seeing some weird behavior.",
        "Quick question — what's the expected behavior for {topic}?",
        "Anyone know who owns {topic}? Need to coordinate.",
        "Is {topic} supposed to work like that, or is it a bug?",
        "Heads up — {topic} might need attention before the release.",
    ],
    "decision": [
        "After discussing with the team, we're going with {option}.",
        "Decision: {option}. We'll revisit in two weeks if needed.",
        "Locking in {option} as the approach. @{person} will lead implementation.",
        "We agreed: {option}. Shipping this behind a feature flag first.",
    ],
    "blocker": [
        "Blocked on {topic} — need {person} to review before I can proceed.",
        "Can't move forward on {topic} until we get access to {resource}.",
        "Waiting on {topic} from {person}. This blocks the {milestone} deadline.",
        "Stuck on {topic}. The {resource} is timing out intermittently.",
    ],
    "casual": [
        "Nice work on {topic} @{person}!",
        "FYI: {topic} was updated in the latest release.",
        "Anyone free for a quick sync on {topic}?",
        "Shared the {topic} doc in the channel. Comments welcome.",
        "Reminder: {topic} review meeting at 2pm.",
    ],
}

TOPICS = [
    "the auth migration", "database indexing", "the onboarding flow",
    "API rate limiting", "the mobile layout", "CI pipeline", "SSL certs",
    "the search feature", "user permissions", "the dashboard redesign",
    "caching layer", "the notification system", "deployment automation",
    "the analytics pipeline", "error handling", "the billing integration",
    "performance monitoring", "the export feature", "load balancing",
    "the settings page", "data backup strategy", "the inventory module",
    "patient scheduling", "the intake form", "records migration",
    "the appointment system", "lab results integration", "prescription tracking",
]

TASKS = [
    "finished the PR for auth refactor", "updated the migration scripts",
    "reviewed the design spec", "fixed the flaky test suite",
    "deployed staging build", "set up monitoring alerts",
    "wrote integration tests", "updated the API docs",
    "refactored the data layer", "optimized the query performance",
    "completed the UI redesign", "set up the dev environment",
    "fixed the memory leak", "updated dependencies", "reviewed code changes",
    "implemented the new endpoint", "resolved the merge conflicts",
]

BLOCKERS = [
    "waiting on vendor API keys", "staging environment is down",
    "need design approval", "dependency upgrade broke tests",
    "access to production logs", "VPN issues blocking deploys",
]

RESOURCES = ["staging", "production DB", "the vendor API", "design assets", "the test cluster"]
MILESTONES = ["sprint", "Q2", "beta launch", "release candidate", "demo day"]
OPTIONS = [
    "microservices over monolith", "React for the frontend",
    "Postgres over MongoDB", "weekly releases", "the phased rollout",
    "keeping the current architecture", "Kubernetes for orchestration",
    "the simplified approach", "automated testing first",
]

# Public GitHub repos with active commits (for git simulator)
PUBLIC_REPOS = [
    "microsoft/vscode", "facebook/react", "golang/go",
    "kubernetes/kubernetes", "rust-lang/rust", "vercel/next.js",
    "django/django", "pallets/flask", "torvalds/linux",
]


class GitSimulator:
    """Pull real commits from public GitHub repos and push as git stream events."""

    def __init__(self, backend, team_id: str, who: str,
                 repos: list[str] = None, interval: float = 60.0,
                 verbose: bool = False):
        self.backend = backend
        self.team_id = team_id
        self.who = who
        self.repos = repos or random.sample(PUBLIC_REPOS, min(3, len(PUBLIC_REPOS)))
        self.interval = interval
        self.verbose = verbose
        self.stats = {"events": 0, "errors": 0}

    def run(self):
        """Pull commits and push as git events."""
        tag = f"[git-sim {self.who}]"
        for repo in self.repos:
            try:
                commits = self._fetch_commits(repo, limit=10)
                if not commits:
                    continue
                if self.verbose:
                    print(f"  {tag} got {len(commits)} commits from {repo}")

                events = []
                for c in commits:
                    msg = c.get("commit", {}).get("message", "").split("\n")[0][:120]
                    author = c.get("commit", {}).get("author", {}).get("name", "unknown")
                    files_changed = c.get("stats", {}).get("total", 0)
                    sha = c.get("sha", "")[:7]

                    events.append({
                        "team_id": self.team_id,
                        "session_id": f"git_{repo.replace('/', '_')}",
                        "stream": "git",
                        "event_type": "commit",
                        "who": self.who,
                        "area": repo.split("/")[1],
                        "files": [],
                        "summary": f"[{sha}] {msg} (by {author})",
                        "raw": {
                            "source": "git_simulator",
                            "repo": repo,
                            "sha": sha,
                            "author": author,
                            "priority": "info",
                        },
                    })

                if events:
                    # Push as a batch summary
                    self.backend.push_events([{
                        "team_id": self.team_id,
                        "session_id": f"git_{repo.replace('/', '_')}",
                        "stream": "git",
                        "event_type": "session_batch",
                        "who": self.who,
                        "summary": f"{len(events)} commits on {repo.split('/')[1]}",
                        "raw": {
                            "source": "git_simulator",
                            "priority": "info",
                            "items": [
                                {"category": "Commits", "text": e["summary"]}
                                for e in events
                            ],
                        },
                    }])
                    self.stats["events"] += 1

            except Exception as e:
                self.stats["errors"] += 1
                if self.verbose:
                    print(f"  {tag} error on {repo}: {e}")

            time.sleep(self.interval / len(self.repos))

    def _fetch_commits(self, repo: str, limit: int = 10) -> list:
        try:
            resp = httpx.get(
                f"https://api.github.com/repos/{repo}/commits",
                params={"per_page": str(limit)},
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=15.0,
            )
            return resp.json() if resp.status_code == 200 else []
        except Exception:
            return []


class SlackSimulator:
    """Generate realistic Slack-like threaded conversations and push as events."""

    def __init__(self, backend, team_id: str, people: list[str],
                 channels: int = 3, messages_per_channel: int = 15,
                 interval: float = 30.0, verbose: bool = False):
        self.backend = backend
        self.team_id = team_id
        self.people = people
        self.channels = channels
        self.messages_per_channel = messages_per_channel
        self.interval = interval
        self.verbose = verbose
        self.stats = {"events": 0, "errors": 0}

    def run(self):
        """Generate and push Slack-style conversations."""
        tag = "[slack-sim]"
        channel_names = random.sample([
            "#engineering", "#product", "#general", "#design",
            "#ops", "#support", "#standup", "#random", "#launches",
            "#incidents", "#frontend", "#backend", "#mobile",
        ], self.channels)

        for channel in channel_names:
            try:
                messages = self._generate_conversation(channel)
                if self.verbose:
                    print(f"  {tag} generated {len(messages)} messages in {channel}")

                # Group into a batch
                items = []
                for msg in messages:
                    category = msg["type"].replace("_", " ").title()
                    items.append({
                        "category": category,
                        "text": f"[{channel}] {msg['who']}: {msg['text']}",
                    })

                # Determine priority from message types
                has_blocker = any(m["type"] == "blocker" for m in messages)
                has_decision = any(m["type"] == "decision" for m in messages)
                priority = "critical" if has_blocker else "warning" if has_decision else "info"

                # Build summary from most important messages
                important = [m for m in messages if m["type"] in ("blocker", "decision", "question")]
                if not important:
                    important = messages[:3]
                summary = " | ".join(m["text"][:80] for m in important[:3])

                # Use the most active person in the conversation as the author
                who_counts = {}
                for m in messages:
                    who_counts[m["who"]] = who_counts.get(m["who"], 0) + 1
                top_who = max(who_counts, key=who_counts.get)

                self.backend.push_events([{
                    "team_id": self.team_id,
                    "session_id": f"slack_{channel.replace('#', '')}",
                    "stream": "slack",
                    "event_type": "session_batch",
                    "who": top_who,
                    "area": channel,
                    "summary": summary,
                    "raw": {
                        "source": "slack_simulator",
                        "channel": channel,
                        "priority": priority,
                        "items": items,
                    },
                }])
                self.stats["events"] += 1

            except Exception as e:
                self.stats["errors"] += 1
                if self.verbose:
                    print(f"  {tag} error: {e}")

            time.sleep(self.interval)

    def _generate_conversation(self, channel: str) -> list[dict]:
        """Generate a realistic channel conversation."""
        messages = []
        topic = random.choice(TOPICS)

        for _ in range(self.messages_per_channel):
            who = random.choice(self.people)
            msg_type = random.choices(
                ["standup", "question", "decision", "blocker", "casual"],
                weights=[15, 25, 10, 10, 40],
            )[0]

            template = random.choice(SLACK_TEMPLATES[msg_type])
            text = template.format(
                topic=topic,
                task1=random.choice(TASKS),
                task2=random.choice(TASKS),
                blocker=random.choice(BLOCKERS),
                person=random.choice([p for p in self.people if p != who] or self.people),
                option=random.choice(OPTIONS),
                resource=random.choice(RESOURCES),
                milestone=random.choice(MILESTONES),
            )

            messages.append({"who": who, "text": text, "type": msg_type})

            # Sometimes switch topics mid-conversation
            if random.random() < 0.3:
                topic = random.choice(TOPICS)

        return messages


class P4VSimulator:
    """Simulate Perforce-style changelists from Git data."""

    def __init__(self, backend, team_id: str, who: str,
                 interval: float = 90.0, verbose: bool = False):
        self.backend = backend
        self.team_id = team_id
        self.who = who
        self.interval = interval
        self.verbose = verbose
        self.stats = {"events": 0, "errors": 0}

    def run(self):
        """Generate P4V-style events."""
        tag = f"[p4v-sim {self.who}]"

        depots = ["//depot/main", "//depot/dev", "//depot/art", "//depot/tools"]
        actions = ["edit", "add", "delete", "integrate", "branch"]
        file_types = [
            "Source/Core/Engine.cpp", "Content/Maps/Level01.umap",
            "Art/Characters/Hero_Mesh.fbx", "Config/DefaultGame.ini",
            "Source/UI/HUD_Widget.cpp", "Content/Blueprints/BP_Player.uasset",
            "Tools/BuildScript.py", "Source/Audio/SoundManager.cpp",
            "Art/Textures/T_Environment.png", "Source/Network/Replication.cpp",
        ]

        changelists = random.randint(3, 8)
        items = []

        for cl in range(changelists):
            depot = random.choice(depots)
            num_files = random.randint(1, 6)
            files = random.sample(file_types, min(num_files, len(file_types)))
            action = random.choice(actions)
            desc = random.choice([
                f"Fixed animation bug in character controller",
                f"Updated lighting for {random.choice(['Level01', 'Level02', 'Arena'])}",
                f"Refactored {random.choice(['input system', 'UI framework', 'asset pipeline'])}",
                f"Added new {random.choice(['weapon', 'ability', 'particle effect'])}",
                f"Performance optimization for {random.choice(['rendering', 'physics', 'AI'])}",
                f"Merged from {random.choice(['main', 'dev', 'feature/combat'])}",
                f"Build fix for {random.choice(['Windows', 'console', 'shipping'])} config",
            ])

            items.append({
                "category": "Changelist",
                "text": f"CL#{10000 + cl} ({action}) {desc} [{len(files)} files in {depot}]",
            })

        summary = " | ".join(i["text"][:80] for i in items[:3])
        self.backend.push_events([{
            "team_id": self.team_id,
            "session_id": f"p4v_{self.who.lower().replace(' ', '_')}",
            "stream": "p4v",
            "event_type": "session_batch",
            "who": self.who,
            "area": "depot",
            "summary": summary,
            "raw": {
                "source": "p4v_simulator",
                "priority": "info",
                "items": items,
            },
        }])
        self.stats["events"] += 1
        if self.verbose:
            print(f"  {tag} pushed {changelists} changelists")


class EmailSimulator:
    """Generate synthetic email digest events."""

    def __init__(self, backend, team_id: str, who: str,
                 interval: float = 120.0, verbose: bool = False):
        self.backend = backend
        self.team_id = team_id
        self.who = who
        self.interval = interval
        self.verbose = verbose
        self.stats = {"events": 0, "errors": 0}

    def run(self):
        """Generate email digest events."""
        subjects = [
            ("RE: Sprint planning for next week", "planning", "info"),
            ("URGENT: Production incident — API latency spike", "incident", "critical"),
            ("Meeting notes: Design review", "meeting", "info"),
            ("Action required: Security audit findings", "security", "warning"),
            ("FYI: New hire starting Monday", "team", "info"),
            ("Vendor contract renewal — decision needed by Friday", "business", "warning"),
            ("RE: Client feedback on latest build", "client", "warning"),
            ("Updated: Project timeline shifted 2 weeks", "planning", "warning"),
            ("Invitation: Quarterly business review", "meeting", "info"),
            ("Build failed on main branch", "ci", "warning"),
        ]

        num_emails = random.randint(3, 7)
        selected = random.sample(subjects, min(num_emails, len(subjects)))

        items = []
        top_priority = "info"
        for subject, category, priority in selected:
            sender = random.choice([
                "sarah.johnson@company.com", "mike.chen@company.com",
                "jira@company.atlassian.net", "buildbot@ci.company.com",
                "ceo@company.com", "hr@company.com",
                "client-feedback@company.com", "ops-alerts@company.com",
            ])
            items.append({
                "category": f"Email ({category})",
                "text": f"From {sender}: {subject}",
            })
            if priority == "critical":
                top_priority = "critical"
            elif priority == "warning" and top_priority != "critical":
                top_priority = "warning"

        important = [i for i in items if "URGENT" in i["text"] or "Action required" in i["text"]]
        if not important:
            important = items[:2]
        summary = " | ".join(i["text"][:80] for i in important[:3])

        self.backend.push_events([{
            "team_id": self.team_id,
            "session_id": f"email_{self.who.lower().replace(' ', '_')}",
            "stream": "email",
            "event_type": "session_batch",
            "who": self.who,
            "area": "inbox",
            "summary": summary,
            "raw": {
                "source": "email_simulator",
                "priority": top_priority,
                "items": items,
            },
        }])
        self.stats["events"] += 1
        if self.verbose:
            print(f"  [email-sim {self.who}] pushed {num_emails} email digest")
