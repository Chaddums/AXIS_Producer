"""Workspace Insights — meta-analysis of event patterns to help teams tune their setup.

Periodically analyzes the team's event stream and generates recommendations:
- Which categories are most/least used (suggest removing unused, adding missing)
- Priority distribution (too many criticals? nothing urgent ever?)
- Per-person patterns (who generates what types of items)
- Timing patterns (when are decisions made vs questions raised)
- Suggested terminology changes based on actual language used
- Configuration suggestions (monitors to enable/disable)

Runs as a background task, pushes insight events to the feed.
"""

import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from llm_provider import call_llm, DEFAULT_MODELS


INSIGHT_PROMPT = """\
You are an AI workspace analyst. You've been given a summary of a team's recent activity
captured by AXIS Producer (a meeting/session recording tool).

Analyze the patterns and generate actionable recommendations to help the team
get more value from the tool. Focus on:

1. **Category fit**: Are the current categories capturing what matters? Should any be
   renamed to match how this team actually talks? Are any categories consistently empty
   (suggest removing) or overloaded (suggest splitting)?

2. **Priority calibration**: Is the priority distribution healthy? If everything is
   "info", the feed is noise. If everything is "critical", nothing stands out.
   Suggest recalibration if needed.

3. **Engagement patterns**: Who contributes what? Are there team members whose input
   is categorized poorly? Is anyone's signal getting buried?

4. **Missing signals**: Based on the content, are there things the team discusses that
   don't fit any current category? Suggest new categories if warranted.

5. **Configuration suggestions**: Based on what's being captured, should any monitors
   be turned on/off? Are there integrations that would help?

Be specific and actionable. Reference actual data from the summary.
Output 3-5 bullet points max. No fluff.

Current workspace type: {workspace_type}
Current categories: {categories}
"""


class WorkspaceInsights:
    """Analyzes event patterns and generates workspace tuning recommendations."""

    def __init__(self, backend_client, team_id: str, settings,
                 interval: int = 3600, verbose: bool = False):
        self.backend = backend_client
        self.team_id = team_id
        self.settings = settings
        self.interval = interval  # seconds between analysis runs
        self.verbose = verbose
        self._stop = threading.Event()

    def start(self):
        """Start the insights analyzer in a background thread."""
        t = threading.Thread(target=self._run_loop, name="workspace-insights", daemon=True)
        t.start()
        return t

    def stop(self):
        self._stop.set()

    def _run_loop(self):
        # Wait a bit before first analysis to let events accumulate
        self._stop.wait(timeout=min(self.interval, 300))
        while not self._stop.is_set():
            try:
                self._analyze()
            except Exception as e:
                if self.verbose:
                    print(f"  [insights] error: {e}")
            self._stop.wait(timeout=self.interval)

    def _analyze(self):
        """Pull recent events, compute stats, generate recommendations."""
        # Pull recent events (last 24h worth)
        since = (datetime.utcnow() - timedelta(hours=24)).isoformat() + "Z"
        events = self.backend.poll_events(self.team_id, since=since, limit=500)
        if not events:
            if self.verbose:
                print("  [insights] no events to analyze")
            return

        # Filter out presence/system events
        content_events = [e for e in events if e.get("stream") != "presence"
                          and e.get("event_type") != "presence"]
        if len(content_events) < 10:
            if self.verbose:
                print(f"  [insights] only {len(content_events)} content events, skipping")
            return

        # Compute stats
        stats = self._compute_stats(content_events)

        # Generate insights via LLM
        summary = self._format_stats(stats)
        recommendations = self._get_recommendations(summary)

        if recommendations:
            # Push as a special insight event
            self.backend.push_events([{
                "team_id": self.team_id,
                "session_id": "workspace_insights",
                "stream": "system",
                "event_type": "insight",
                "who": "AXIS",
                "area": "Workspace Insights",
                "summary": recommendations,
                "raw": {
                    "source": "workspace_insights",
                    "priority": "info",
                    "stats": stats,
                },
            }])
            if self.verbose:
                print(f"  [insights] pushed recommendations")

    def _compute_stats(self, events: list[dict]) -> dict:
        """Compute event pattern statistics."""
        category_counts = Counter()
        type_counts = Counter()
        who_counts = Counter()
        who_categories = defaultdict(Counter)
        priority_counts = Counter()
        hourly = Counter()
        summaries_by_category = defaultdict(list)

        for e in events:
            cat = e.get("area", "uncategorized")
            et = e.get("event_type", "unknown")
            who = e.get("who", "unknown")
            priority = e.get("raw", {}).get("priority", "info")
            ts = e.get("ts", "")

            category_counts[cat] += 1
            type_counts[et] += 1
            who_counts[who] += 1
            who_categories[who][cat] += 1
            priority_counts[priority] += 1

            if ts:
                try:
                    hour = datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
                    hourly[hour] += 1
                except Exception:
                    pass

            summary = e.get("summary", "")
            if summary and len(summaries_by_category[cat]) < 5:
                summaries_by_category[cat].append(summary[:100])

        return {
            "total_events": len(events),
            "categories": dict(category_counts.most_common()),
            "event_types": dict(type_counts.most_common()),
            "contributors": dict(who_counts.most_common()),
            "contributor_categories": {
                who: dict(cats.most_common(5))
                for who, cats in who_categories.items()
            },
            "priorities": dict(priority_counts),
            "hourly_distribution": dict(sorted(hourly.items())),
            "sample_summaries": {k: v for k, v in summaries_by_category.items()},
        }

    def _format_stats(self, stats: dict) -> str:
        """Format stats into a readable summary for the LLM."""
        lines = []
        lines.append(f"Total events in last 24h: {stats['total_events']}")
        lines.append("")

        lines.append("Category distribution:")
        for cat, count in stats["categories"].items():
            pct = count / stats["total_events"] * 100
            lines.append(f"  {cat}: {count} ({pct:.0f}%)")
        lines.append("")

        lines.append("Priority distribution:")
        for pri, count in stats["priorities"].items():
            lines.append(f"  {pri}: {count}")
        lines.append("")

        lines.append(f"Contributors: {len(stats['contributors'])}")
        for who, count in list(stats["contributors"].items())[:10]:
            cats = stats["contributor_categories"].get(who, {})
            top_cats = ", ".join(f"{c}({n})" for c, n in list(cats.items())[:3])
            lines.append(f"  {who}: {count} events — {top_cats}")
        lines.append("")

        if stats["hourly_distribution"]:
            peak_hour = max(stats["hourly_distribution"], key=stats["hourly_distribution"].get)
            lines.append(f"Peak activity hour: {peak_hour}:00")
        lines.append("")

        lines.append("Sample content per category:")
        for cat, samples in list(stats["sample_summaries"].items())[:5]:
            lines.append(f"  [{cat}]")
            for s in samples[:2]:
                lines.append(f"    - {s}")

        return "\n".join(lines)

    def _get_recommendations(self, stats_summary: str) -> str:
        """Ask the LLM to analyze patterns and suggest improvements."""
        categories = list(self.settings.output_terminology.keys()) if self.settings.output_terminology else [
            "Decisions Locked", "Ideas Generated", "Open Questions",
            "Action Items", "Watch List", "Blockers", "Key Discussion",
        ]

        prompt = INSIGHT_PROMPT.format(
            workspace_type=self.settings.workspace_type,
            categories=", ".join(categories),
        )

        try:
            return call_llm(
                provider=self.settings.llm_provider,
                system=prompt,
                user_message=stats_summary,
                api_key=self.settings.llm_api_key,
                model=self.settings.llm_model,
                max_tokens=512,
                ollama_url=self.settings.ollama_url,
            )
        except Exception as e:
            if self.verbose:
                print(f"  [insights] LLM error: {e}")
            return ""


def run_once(backend_client, team_id: str, settings, verbose: bool = True) -> str:
    """Run a single insights analysis and return the recommendations. Useful for testing."""
    analyzer = WorkspaceInsights(backend_client, team_id, settings, verbose=verbose)
    analyzer._analyze()
    return "Analysis complete — check the dashboard for insight events."
