#!/usr/bin/env python3
"""Session Digest — post-processes AXIS Producer session logs into themed, searchable notes.

Parses session_log.md, sends items to Claude for theming by project category,
writes accumulated output to session-digest.md, and indexes everything into
a local SQLite + FTS5 database for instant search.

Usage:
    python digest.py session_log.md                    # theme + triage + index
    python digest.py session_log.md --dry-run          # preview without writing
    python digest.py --search "fog decisions"          # full-text search
    python digest.py --search "sensor" --tag DECISION  # search + filter by tag
    python digest.py --recent                          # show recent items
    python digest.py --stats                           # show index stats
    python digest.py --train "Game Systems" "vine,node,signal"  # add routing keywords
    python digest.py --triage-stats                    # show triage analytics
"""

import argparse
import os
import re
import sys
from datetime import datetime

import anthropic

from digest_db import DigestDB, DEFAULT_DB_PATH
from triage import (
    triage_session, load_training, train as triage_train,
    TriageAnalytics, TRAINING_PATH, ANALYTICS_PATH,
)

MODEL = "claude-sonnet-4-20250514"
MAX_TOKENS = 2048

# Default output: Claude Code memory directory for this project
DEFAULT_OUTPUT = os.path.normpath(os.path.join(
    os.path.expanduser("~"),
    ".claude", "projects",
    "C--Users-Stu-GitHub-Crawler-Project",
    "memory", "session-digest.md"
))

THEME_CATEGORIES = [
    "Game Systems",
    "Content",
    "Visual Polish",
    "Balance / Tuning",
    "Architecture / Code",
    "AXIS / Lore",
]

TAG_MAP = {
    "Decisions Locked": "DECISION",
    "Ideas Generated": "IDEA",
    "Open Questions": "QUESTION",
    "Action Items": "ACTION",
    "Watch List": "WATCH",
    "Blockers": "BLOCKER",
}

THEMING_PROMPT = """\
You are organizing game development session notes into themed categories.

Given the raw session notes below, re-sort every item into one of these project themes:
- Game Systems (mechanics, logic, gameplay loops)
- Content (maps, enemies, waves, items, progression)
- Visual Polish (UI, VFX, models, shaders, animations)
- Balance / Tuning (numbers, difficulty, pacing, economy)
- Architecture / Code (refactors, patterns, tech debt, tools)
- AXIS / Lore (story, dialogue, AXIS personality, world-building)

Preserve each item's original tag exactly: [DECISION], [IDEA], [ACTION], [QUESTION], or [WATCH].
Preserve the timestamp in parentheses if present.

After theming, add a "Suggested Updates" section listing which project files should be updated:
- ALPHA_ROADMAP.md — for scope changes, completed items, new tasks
- CLAUDE_COORD.md — for architecture decisions, convention changes
- vine-td-backlog.md — for new backlog items, priority changes

Only suggest updates that are clearly warranted by the notes. If nothing warrants an update, omit the section.

Output format (omit empty categories):

### Game Systems
- [TAG] item text (HH:MM)

### Content
- [TAG] item text (HH:MM)

...

### Suggested Updates
- ALPHA_ROADMAP.md — reason
"""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_session_log(text: str) -> list[dict]:
    """Parse session_log.md into a list of batch dicts.

    Each batch dict has:
        time: str (HH:MM)
        batch_num: int
        items: list[dict] with keys: tag, text
    """
    batches = []
    batch_pattern = re.compile(r"^## \[(\d{2}:\d{2})\] Batch (\d+)", re.MULTILINE)
    section_pattern = re.compile(r"^## (Decisions Locked|Ideas Generated|Open Questions|Action Items|Watch List|Blockers)", re.MULTILINE)
    item_pattern = re.compile(r"^- (.+)$", re.MULTILINE)

    batch_splits = list(batch_pattern.finditer(text))
    if not batch_splits:
        return batches

    for i, match in enumerate(batch_splits):
        start = match.start()
        end = batch_splits[i + 1].start() if i + 1 < len(batch_splits) else len(text)
        block = text[start:end]

        batch = {
            "time": match.group(1),
            "batch_num": int(match.group(2)),
            "items": [],
        }

        sections = list(section_pattern.finditer(block))
        for j, sec_match in enumerate(sections):
            tag = TAG_MAP.get(sec_match.group(1), "NOTE")
            sec_start = sec_match.end()
            sec_end = sections[j + 1].start() if j + 1 < len(sections) else len(block)
            sec_text = block[sec_start:sec_end]

            for item_match in item_pattern.finditer(sec_text):
                batch["items"].append({
                    "tag": tag,
                    "text": item_match.group(1).strip(),
                })

        if batch["items"]:
            batches.append(batch)

    return batches


def extract_session_header(text: str) -> str:
    """Extract session start time from the log header."""
    m = re.search(r"Started:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})", text)
    if m:
        return m.group(1)
    return datetime.now().strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------------------
# Theming
# ---------------------------------------------------------------------------

def format_items_for_claude(batches: list[dict]) -> str:
    """Format parsed batches into a flat list for the theming prompt."""
    lines = []
    for batch in batches:
        for item in batch["items"]:
            lines.append(f"[{item['tag']}] {item['text']} ({batch['time']})")
    return "\n".join(lines)


def theme_items(items_text: str, dry_run: bool = False) -> str:
    """Send items to Claude for theming. Returns themed markdown."""
    if not items_text.strip():
        return ""

    if dry_run and not os.environ.get("ANTHROPIC_API_KEY"):
        return f"### Unthemed (no API key)\n{items_text}\n"

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=THEMING_PROMPT,
        messages=[{"role": "user", "content": items_text}],
    )
    return response.content[0].text


def _theme_items_locally(triage_results: list, batches: list[dict]) -> str:
    """Theme items using local triage scoring instead of LLM call.

    Builds the same markdown format that parse_themed_output() expects,
    but using the theme assignments from triage.route_to_theme().
    """
    if not triage_results:
        return ""

    # Build time lookup from batches
    time_lookup = {}
    for batch in batches:
        for item in batch["items"]:
            time_lookup[item["text"][:60].lower()] = batch["time"]

    # Group by theme
    by_theme = {}
    for tr in triage_results:
        theme = tr.get("theme", "Other") or "Other"
        if theme not in by_theme:
            by_theme[theme] = []
        tag = tr.get("tag", "DISCUSSION")
        text = tr.get("text", "")
        time = time_lookup.get(text[:60].lower(), "")
        by_theme[theme].append({"tag": tag, "text": text, "time": time})

    # Format as markdown (same format as LLM output)
    lines = []
    for theme, items in sorted(by_theme.items()):
        lines.append(f"### {theme}")
        for item in items:
            time_str = f" ({item['time']})" if item["time"] else ""
            lines.append(f"- [{item['tag']}] {item['text']}{time_str}")
        lines.append("")

    return "\n".join(lines)


def parse_themed_output(themed_text: str) -> list[dict]:
    """Parse Claude's themed output back into structured items.

    Returns list of dicts with: tag, theme, text, time
    """
    items = []
    current_theme = ""
    theme_pattern = re.compile(r"^###\s+(.+)$", re.MULTILINE)
    item_pattern = re.compile(r"^- \[(\w+)\]\s+(.+?)(?:\s+\((\d{2}:\d{2})\))?\s*$", re.MULTILINE)

    # Find all theme headers and their positions
    theme_positions = [(m.start(), m.group(1).strip()) for m in theme_pattern.finditer(themed_text)]

    for idx, (pos, theme) in enumerate(theme_positions):
        if theme == "Suggested Updates":
            continue
        end_pos = theme_positions[idx + 1][0] if idx + 1 < len(theme_positions) else len(themed_text)
        section = themed_text[pos:end_pos]

        for m in item_pattern.finditer(section):
            items.append({
                "tag": m.group(1).upper(),
                "theme": theme,
                "text": m.group(2).strip(),
                "time": m.group(3) or "",
            })

    return items


# ---------------------------------------------------------------------------
# Markdown output
# ---------------------------------------------------------------------------

def format_session_block(session_date: str, end_time: str, themed_text: str) -> str:
    """Format a single session's themed output."""
    return f"## Session: {session_date} (ended {end_time})\n\n{themed_text}\n\n---\n\n"


def read_existing_digest(path: str) -> str:
    """Read existing digest file, or return empty string."""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def write_digest(path: str, new_session_block: str):
    """Append a new session block to the digest file, creating it if needed."""
    existing = read_existing_digest(path)

    if not existing:
        content = DIGEST_FRONTMATTER + new_session_block
    else:
        content = existing.rstrip() + "\n\n" + new_session_block

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


DIGEST_FRONTMATTER = """\
---
name: Session Digest
description: Themed notes from AXIS Producer voice sessions — decisions, ideas, actions, questions
type: project
---

# Session Digest

Accumulated notes from voice codev sessions, themed by project area.
Each session's items are tagged: [DECISION], [IDEA], [ACTION], [QUESTION], [WATCH].

---

"""


# ---------------------------------------------------------------------------
# DB indexing
# ---------------------------------------------------------------------------

def index_to_db(session_date: str, batches: list[dict], themed_text: str,
                db_path: str = DEFAULT_DB_PATH, triage_results: list = None,
                verbose: bool = False):
    """Parse themed output and insert items into the search index."""
    db = DigestDB(db_path)
    try:
        themed_items = parse_themed_output(themed_text)

        # Build a lookup from triage results by text prefix (for score/grade)
        triage_lookup = {}
        if triage_results:
            for tr in triage_results:
                key = tr["text"][:60].lower()
                triage_lookup[key] = {"score": tr["score"], "grade": tr["grade"]}

        def _enrich(row):
            """Add triage score/grade if available."""
            key = row["text"][:60].lower()
            tr = triage_lookup.get(key, {})
            row["triage_score"] = tr.get("score", 0)
            row["triage_grade"] = tr.get("grade", "")
            return row

        if themed_items:
            rows = []
            for item in themed_items:
                rows.append(_enrich({
                    "session_date": session_date,
                    "batch_time": item["time"],
                    "tag": item["tag"],
                    "theme": item["theme"],
                    "text": item["text"],
                }))
            db.insert_items(rows)
            if verbose:
                print(f"  [digest] indexed {len(rows)} themed items to DB")
        else:
            rows = []
            for batch in batches:
                for item in batch["items"]:
                    rows.append(_enrich({
                        "session_date": session_date,
                        "batch_time": batch["time"],
                        "tag": item["tag"],
                        "theme": "",
                        "text": item["text"],
                    }))
            db.insert_items(rows)
            if verbose:
                print(f"  [digest] indexed {len(rows)} raw items to DB (theming parse failed)")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Search CLI
# ---------------------------------------------------------------------------

def format_results(results: list[dict]) -> str:
    """Format search results for terminal display."""
    if not results:
        return "  No results found."

    lines = []
    for r in results:
        theme = f" ({r['theme']})" if r.get("theme") else ""
        time = f" {r['batch_time']}" if r.get("batch_time") else ""
        grade = r.get("triage_grade", "")
        score = r.get("triage_score", 0)
        triage_info = f" [{grade} {score}/100]" if grade else ""
        lines.append(f"  [{r['tag']}]{theme}{time}{triage_info} — {r['text']}")
        lines.append(f"         session: {r['session_date']}")
    return "\n".join(lines)


def cmd_search(query: str, tag: str = None, theme: str = None,
               grade: str = None, limit: int = 20, db_path: str = DEFAULT_DB_PATH):
    """Run a search and print results."""
    db = DigestDB(db_path)
    try:
        if grade:
            results = db.search_by_grade(grade, limit=limit)
            if query:
                query_lower = query.lower()
                results = [r for r in results if query_lower in r["text"].lower()]
        elif tag:
            results = db.search_by_tag(tag, limit=limit)
            if query:
                query_lower = query.lower()
                results = [r for r in results if query_lower in r["text"].lower()]
        elif theme:
            results = db.search_by_theme(theme, limit=limit)
            if query:
                query_lower = query.lower()
                results = [r for r in results if query_lower in r["text"].lower()]
        elif query:
            results = db.search(query, limit=limit)
        else:
            results = db.recent(limit=limit)

        print(format_results(results))
    finally:
        db.close()


def cmd_train(theme: str, keywords_csv: str):
    """Add keywords to a theme's routing table."""
    keywords = [kw.strip() for kw in keywords_csv.split(",") if kw.strip()]
    if not keywords:
        print("ERROR: no keywords provided")
        return
    updated = triage_train(theme, keywords)
    print(f"  Added {len(keywords)} keywords to '{theme}'")
    print(f"  Total keywords for '{theme}': {len(updated.get(theme, []))}")


def cmd_triage_stats():
    """Print triage analytics summary."""
    analytics = TriageAnalytics()
    s = analytics.get_summary()

    print(f"\n  Total items triaged: {s['total_processed']}")
    print(f"  Sessions: {s['session_count']}")

    if s["by_grade"]:
        print("\n  By grade:")
        for grade, count in s["by_grade"].items():
            print(f"    {grade}: {count}")

    if s["by_theme"]:
        print("\n  By theme:")
        for theme, count in sorted(s["by_theme"].items(), key=lambda x: -x[1]):
            print(f"    {theme}: {count}")

    if s["by_tag"]:
        print("\n  By tag:")
        for tag, count in s["by_tag"].items():
            print(f"    [{tag}] {count}")

    if s["avg_score_trend"]:
        print(f"\n  Recent avg scores: {s['avg_score_trend']}")
    print()


def cmd_stats(db_path: str = DEFAULT_DB_PATH):
    """Print index statistics."""
    db = DigestDB(db_path)
    try:
        s = db.stats()
        print(f"\n  Total items: {s['total']}")
        print(f"  Sessions: {len(s['sessions'])}")
        if s["by_tag"]:
            print("\n  By tag:")
            for tag, count in s["by_tag"].items():
                print(f"    [{tag}] {count}")
        if s["by_theme"]:
            print("\n  By theme:")
            for theme, count in s["by_theme"].items():
                label = theme if theme else "(unthemed)"
                print(f"    {label}: {count}")
        if s["sessions"]:
            print("\n  Sessions:")
            for sess in s["sessions"]:
                print(f"    {sess}")
        print()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_digest(log_path: str, output_path: str, dry_run: bool = False,
               db_path: str = DEFAULT_DB_PATH, verbose: bool = False) -> bool:
    """Main digest pipeline. Returns True on success."""
    if not os.path.exists(log_path):
        print(f"ERROR: session log not found: {log_path}")
        return False

    with open(log_path, "r", encoding="utf-8") as f:
        raw = f.read()

    session_start = extract_session_header(raw)
    batches = parse_session_log(raw)

    if not batches:
        if verbose:
            print("  [digest] no batches found in session log")
        return True

    total_items = sum(len(b["items"]) for b in batches)
    if verbose:
        print(f"  [digest] parsed {len(batches)} batches, {total_items} items")

    # Triage: score and route all items locally (no API call)
    flat_items = []
    for batch in batches:
        for item in batch["items"]:
            flat_items.append({**item, "time": batch["time"]})

    theme_keywords = load_training()

    # Load user feedback + term weights for scoring adjustments
    feedback_weights = None
    try:
        from user_db import UserDB
        feedback_weights = UserDB().get_effective_weights()
    except Exception:
        pass  # feedback DB not available — score without it

    triage_result = triage_session(flat_items, theme_keywords, feedback_weights)

    if verbose:
        s = triage_result["summary"]
        print(f"  [triage] scored {s['total']} items -- "
              f"avg {s['avg_score']:.0f}/100 — "
              f"grades: {s['by_grade']}")

    # Record analytics
    try:
        analytics = TriageAnalytics()
        analytics.record_session(triage_result["summary"])
    except Exception:
        pass  # analytics failure is non-fatal

    # Theme items locally using triage results (no LLM call needed)
    if verbose:
        print(f"  [digest] theming {total_items} items locally via triage...")

    themed = _theme_items_locally(triage_result.get("results", []), batches)

    if not themed:
        # Fallback: LLM theming if local produced nothing
        if verbose:
            print("  [digest] local theming empty, falling back to LLM...")
        items_text = format_items_for_claude(batches)
        themed = theme_items(items_text, dry_run=dry_run)

    if not themed:
        if verbose:
            print("  [digest] no themed output returned")
        return True

    end_time = batches[-1]["time"]
    session_block = format_session_block(session_start, end_time, themed)

    if dry_run:
        print("\n--- DIGEST DRY RUN ---\n")
        print(session_block)
        print("--- END DRY RUN ---\n")
        return True

    write_digest(output_path, session_block)
    if verbose:
        print(f"  [digest] wrote themed digest to: {output_path}")

    # Index into SQLite for search (include triage scores)
    try:
        index_to_db(session_start, batches, themed, db_path=db_path,
                     triage_results=triage_result.get("results"), verbose=verbose)
    except Exception as e:
        print(f"  [digest] DB indexing failed (non-fatal): {e}")

    return True


def parse_args():
    p = argparse.ArgumentParser(
        description="AXIS Producer Session Digest — theme, triage & search session notes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
examples:
  python digest.py session_log.md              # theme + triage + index
  python digest.py session_log.md --dry-run    # preview without writing
  python digest.py --search "fog decisions"    # full-text search
  python digest.py --search "sensor" --tag DECISION
  python digest.py --grade actionable          # filter by triage grade
  python digest.py --tag ACTION                # all action items
  python digest.py --theme "Game Systems"      # filter by theme
  python digest.py --recent                    # latest items
  python digest.py --stats                     # index overview
  python digest.py --train "Game Systems" "vine,node,signal"
  python digest.py --triage-stats              # triage analytics
""")

    # Digest mode
    p.add_argument("log", nargs="?", default=None,
                   help="Path to session_log.md to digest")
    p.add_argument("--out", default=DEFAULT_OUTPUT,
                   help="Output digest markdown path")
    p.add_argument("--dry-run", action="store_true",
                   help="Print themed output to stdout without writing")

    # Search mode
    p.add_argument("--search", "-s", metavar="QUERY",
                   help="Full-text search the digest index")
    p.add_argument("--tag", "-t", metavar="TAG",
                   help="Filter by tag: DECISION, IDEA, ACTION, QUESTION, WATCH")
    p.add_argument("--theme", metavar="THEME",
                   help="Filter by theme category")
    p.add_argument("--grade", "-g", metavar="GRADE",
                   help="Filter by triage grade: actionable, needs-context, parked, stale")
    p.add_argument("--recent", "-r", action="store_true",
                   help="Show most recent items")
    p.add_argument("--stats", action="store_true",
                   help="Show index statistics")
    p.add_argument("--limit", "-n", type=int, default=20,
                   help="Max results to show (default: 20)")

    # Triage training
    p.add_argument("--train", nargs=2, metavar=("THEME", "KEYWORDS"),
                   help='Add routing keywords: --train "Game Systems" "vine,node,signal"')
    p.add_argument("--triage-stats", action="store_true",
                   help="Show triage analytics summary")

    # Common
    p.add_argument("--db", default=DEFAULT_DB_PATH,
                   help="Path to SQLite database")
    p.add_argument("--verbose", action="store_true",
                   help="Print progress to console")

    return p.parse_args()


def main():
    args = parse_args()

    # Training mode
    if args.train:
        cmd_train(args.train[0], args.train[1])
        return

    # Triage analytics
    if args.triage_stats:
        cmd_triage_stats()
        return

    # Search / query modes
    if args.stats:
        cmd_stats(db_path=args.db)
        return

    if args.recent:
        cmd_search(query=None, limit=args.limit, db_path=args.db)
        return

    if args.search is not None or args.tag or args.theme or args.grade:
        cmd_search(
            query=args.search,
            tag=args.tag,
            theme=args.theme,
            grade=args.grade,
            limit=args.limit,
            db_path=args.db,
        )
        return

    # Digest mode
    log_path = args.log or "./session_log.md"
    success = run_digest(
        log_path=log_path,
        output_path=args.out,
        dry_run=args.dry_run,
        db_path=args.db,
        verbose=True,
    )
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
