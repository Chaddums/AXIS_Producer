#!/usr/bin/env python3
"""Smoke tests for the session digest post-processor.

Run: python test_digest.py
Tests parsing, formatting, themed output parsing, and DB indexing/search.
No API key required.
"""

import os
import sys
import tempfile

# Ensure imports work from this directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from digest import (
    parse_session_log,
    extract_session_header,
    format_items_for_claude,
    format_session_block,
    parse_themed_output,
    index_to_db,
    write_digest,
    read_existing_digest,
)
from digest_db import DigestDB

SAMPLE_LOG = """\
# AXIS Session Log
Started: 2026-03-16 14:00

---

## [14:30] Batch 1

## Decisions Locked
- Sensors use 3-tile detection radius
- Chain signals propagate at 1 tick per node

## Ideas Generated
- Chain sensors amplify signal when adjacent
- AXIS could comment on network topology

## Open Questions
- Should gates consume power?

---

## [15:15] Batch 2

## Action Items
- Need 2 more map layouts for wave variety
- Add sound effects for signal propagation

## Watch List
- Performance concern with large networks (100+ nodes)

## Ideas Generated
- Visual pulse effect along signal paths

---

## [16:00] Batch 3

## Decisions Locked
- Wave 1 starts with 10 enemies, +5 per wave

## Action Items
- Wire up draft screen to battle scene transition

---
"""

SAMPLE_THEMED = """\
### Game Systems
- [DECISION] Sensors use 3-tile detection radius (14:30)
- [DECISION] Chain signals propagate at 1 tick per node (14:30)
- [IDEA] Chain sensors amplify signal when adjacent (14:30)
- [QUESTION] Should gates consume power? (14:30)

### Content
- [ACTION] Need 2 more map layouts for wave variety (15:15)
- [DECISION] Wave 1 starts with 10 enemies, +5 per wave (16:00)

### Visual Polish
- [IDEA] Visual pulse effect along signal paths (15:15)

### Architecture / Code
- [ACTION] Wire up draft screen to battle scene transition (16:00)
- [ACTION] Add sound effects for signal propagation (15:15)

### Balance / Tuning
- [WATCH] Performance concern with large networks (100+ nodes) (15:15)

### AXIS / Lore
- [IDEA] AXIS could comment on network topology (14:30)

### Suggested Updates
- ALPHA_ROADMAP.md — Add wave scaling decision (10 base, +5/wave)
"""

EMPTY_LOG = """\
# AXIS Session Log
Started: 2026-03-16 18:00

---

"""

NOTHING_LOG = """\
# AXIS Session Log
Started: 2026-03-16 18:00

---

## [18:05] Batch 1

[nothing to report]

---
"""


def test_parse_session_log():
    """Test parsing session_log.md into structured batches."""
    print("TEST 1: Parse session log...")

    batches = parse_session_log(SAMPLE_LOG)

    assert len(batches) == 3, f"Expected 3 batches, got {len(batches)}"
    assert batches[0]["time"] == "14:30"
    assert batches[0]["batch_num"] == 1
    assert batches[2]["time"] == "16:00"

    assert len(batches[0]["items"]) == 5, f"Batch 1: expected 5 items, got {len(batches[0]['items'])}"

    tags = [item["tag"] for item in batches[0]["items"]]
    assert tags.count("DECISION") == 2
    assert tags.count("IDEA") == 2
    assert tags.count("QUESTION") == 1

    assert len(batches[1]["items"]) == 4, f"Batch 2: expected 4 items, got {len(batches[1]['items'])}"
    assert len(batches[2]["items"]) == 2

    print(f"  PASS — 3 batches, {sum(len(b['items']) for b in batches)} total items")
    print()


def test_parse_empty_log():
    """Test parsing a log with no batches."""
    print("TEST 2: Parse empty log...")

    batches = parse_session_log(EMPTY_LOG)
    assert len(batches) == 0, f"Expected 0 batches, got {len(batches)}"

    print("  PASS — 0 batches from empty log")
    print()


def test_parse_nothing_to_report():
    """Test parsing a batch with [nothing to report]."""
    print("TEST 3: Parse 'nothing to report' batch...")

    batches = parse_session_log(NOTHING_LOG)
    assert len(batches) == 0 or all(len(b["items"]) == 0 for b in batches)

    print("  PASS — no items extracted from [nothing to report]")
    print()


def test_extract_session_header():
    """Test session start time extraction."""
    print("TEST 4: Extract session header...")

    start = extract_session_header(SAMPLE_LOG)
    assert start == "2026-03-16 14:00", f"Expected '2026-03-16 14:00', got '{start}'"

    print(f"  PASS — extracted: {start}")
    print()


def test_format_items_for_claude():
    """Test formatting parsed items into Claude prompt input."""
    print("TEST 5: Format items for Claude...")

    batches = parse_session_log(SAMPLE_LOG)
    text = format_items_for_claude(batches)

    lines = text.strip().split("\n")
    total_items = sum(len(b["items"]) for b in batches)
    assert len(lines) == total_items, f"Expected {total_items} lines, got {len(lines)}"

    assert lines[0].startswith("[DECISION]")
    assert "(14:30)" in lines[0]

    print(f"  PASS — {len(lines)} formatted lines")
    print(f"  Sample: {lines[0]}")
    print()


def test_format_session_block():
    """Test session block formatting."""
    print("TEST 6: Format session block...")

    block = format_session_block("2026-03-16 14:00", "16:00", "### Game Systems\n- [DECISION] test item")

    assert "## Session: 2026-03-16 14:00" in block
    assert "(ended 16:00)" in block
    assert "### Game Systems" in block
    assert "---" in block

    print("  PASS — session block formatted correctly")
    print()


def test_write_digest_new():
    """Test creating a new digest file."""
    print("TEST 7: Write new digest file...")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()) as f:
        path = f.name

    try:
        os.unlink(path)

        session_block = format_session_block("2026-03-16 14:00", "16:00", "### Game Systems\n- [DECISION] test")
        write_digest(path, session_block)

        content = read_existing_digest(path)
        assert "---\nname: Session Digest" in content, "Missing frontmatter"
        assert "# Session Digest" in content, "Missing title"
        assert "## Session: 2026-03-16 14:00" in content, "Missing session block"

        print("  PASS — new digest created with frontmatter + session block")
    finally:
        if os.path.exists(path):
            os.unlink(path)
    print()


def test_write_digest_append():
    """Test appending to an existing digest file."""
    print("TEST 8: Append to existing digest...")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, dir=tempfile.gettempdir()) as f:
        path = f.name

    try:
        os.unlink(path)

        block1 = format_session_block("2026-03-16 14:00", "16:00", "### Game Systems\n- [DECISION] first")
        write_digest(path, block1)

        block2 = format_session_block("2026-03-17 10:00", "12:00", "### Content\n- [IDEA] second")
        write_digest(path, block2)

        content = read_existing_digest(path)
        assert content.count("## Session:") == 2, "Expected 2 session blocks"

        print("  PASS — 2 session blocks in accumulated digest")
    finally:
        if os.path.exists(path):
            os.unlink(path)
    print()


def test_tag_preservation():
    """Test that all 5 tag types are correctly parsed."""
    print("TEST 9: Tag preservation...")

    batches = parse_session_log(SAMPLE_LOG)
    all_tags = set()
    for batch in batches:
        for item in batch["items"]:
            all_tags.add(item["tag"])

    expected = {"DECISION", "IDEA", "QUESTION", "ACTION", "WATCH"}
    assert all_tags == expected, f"Expected {expected}, got {all_tags}"

    print(f"  PASS — all 5 tags found: {sorted(all_tags)}")
    print()


def test_parse_themed_output():
    """Test parsing Claude's themed markdown back into structured items."""
    print("TEST 10: Parse themed output...")

    items = parse_themed_output(SAMPLE_THEMED)

    assert len(items) == 11, f"Expected 11 items, got {len(items)}"

    # Check themes are assigned
    themes = set(i["theme"] for i in items)
    assert "Game Systems" in themes
    assert "Content" in themes
    assert "Visual Polish" in themes
    assert "Architecture / Code" in themes
    assert "Balance / Tuning" in themes
    assert "AXIS / Lore" in themes

    # Check tags preserved
    tags = set(i["tag"] for i in items)
    assert tags == {"DECISION", "IDEA", "ACTION", "QUESTION", "WATCH"}

    # Check timestamps parsed
    times = [i["time"] for i in items if i["time"]]
    assert len(times) == 11, f"Expected 11 timestamps, got {len(times)}"

    # Suggested Updates section should NOT appear as items
    for item in items:
        assert "ALPHA_ROADMAP" not in item["text"], "Suggested Updates leaked into items"

    print(f"  PASS — {len(items)} items across {len(themes)} themes")
    print()


def test_db_insert_and_search():
    """Test SQLite DB insertion and FTS5 search."""
    print("TEST 11: DB insert + FTS5 search...")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=tempfile.gettempdir()) as f:
        db_path = f.name

    db = None
    try:
        os.unlink(db_path)
        db = DigestDB(db_path)

        db.insert_items([
            {"session_date": "2026-03-16 14:00", "batch_time": "14:30",
             "tag": "DECISION", "theme": "Game Systems",
             "text": "Sensors use 3-tile detection radius"},
            {"session_date": "2026-03-16 14:00", "batch_time": "14:30",
             "tag": "IDEA", "theme": "Game Systems",
             "text": "Chain sensors amplify signal when adjacent"},
            {"session_date": "2026-03-16 14:00", "batch_time": "15:15",
             "tag": "ACTION", "theme": "Content",
             "text": "Need 2 more map layouts for wave variety"},
            {"session_date": "2026-03-16 14:00", "batch_time": "15:15",
             "tag": "WATCH", "theme": "Balance / Tuning",
             "text": "Performance concern with large networks"},
            {"session_date": "2026-03-16 14:00", "batch_time": "14:30",
             "tag": "IDEA", "theme": "AXIS / Lore",
             "text": "AXIS could comment on network topology"},
        ])

        # FTS5 search (prefix matching: "sensor" matches "Sensors")
        results = db.search("sensor")
        assert len(results) >= 1, f"Expected >=1 results for 'sensor', got {len(results)}"
        assert any("sensor" in r["text"].lower() for r in results)

        # Search by tag
        decisions = db.search_by_tag("DECISION")
        assert len(decisions) == 1
        assert "3-tile" in decisions[0]["text"]

        # Search by theme
        game_sys = db.search_by_theme("Game Systems")
        assert len(game_sys) == 2

        # Recent
        recent = db.recent(limit=3)
        assert len(recent) == 3

        # Stats
        stats = db.stats()
        assert stats["total"] == 5
        assert stats["by_tag"]["IDEA"] == 2
        assert "Game Systems" in stats["by_theme"]

        print(f"  PASS — 5 items indexed, search/filter/stats all work")
    finally:
        if db:
            db.close()
        if os.path.exists(db_path):
            os.unlink(db_path)
    print()


def test_db_index_from_themed():
    """Test the full index_to_db pipeline with themed output."""
    print("TEST 12: index_to_db from themed output...")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=tempfile.gettempdir()) as f:
        db_path = f.name

    db = None
    try:
        os.unlink(db_path)
        batches = parse_session_log(SAMPLE_LOG)
        index_to_db("2026-03-16 14:00", batches, SAMPLE_THEMED, db_path=db_path)

        db = DigestDB(db_path)
        stats = db.stats()
        assert stats["total"] == 11, f"Expected 11 indexed items, got {stats['total']}"

        results = db.search("fog")  # not in data — should return 0
        assert len(results) == 0

        results = db.search("topology")
        assert len(results) >= 1

        print(f"  PASS — 11 items indexed from themed output")
    finally:
        if db:
            db.close()
        if os.path.exists(db_path):
            os.unlink(db_path)
    print()


def test_db_index_fallback_raw():
    """Test that raw items get indexed when themed output can't be parsed."""
    print("TEST 13: DB fallback to raw items...")

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False, dir=tempfile.gettempdir()) as f:
        db_path = f.name

    db = None
    try:
        os.unlink(db_path)
        batches = parse_session_log(SAMPLE_LOG)
        index_to_db("2026-03-16 14:00", batches, "This has no themed sections.", db_path=db_path)

        db = DigestDB(db_path)
        stats = db.stats()
        assert stats["total"] == 11, f"Expected 11 raw items, got {stats['total']}"
        assert "" in stats["by_theme"] or stats["total"] > 0

        print(f"  PASS — {stats['total']} raw items indexed as fallback")
    finally:
        if db:
            db.close()
        if os.path.exists(db_path):
            os.unlink(db_path)
    print()


def test_triage_scoring():
    """Test that triage scores items with correct grades."""
    print("TEST 14: Triage scoring...")

    from triage import score_item, triage_session

    # High-quality action item should score well
    good_action = {
        "tag": "ACTION",
        "text": "Need 2 more map layouts for wave variety",
        "time": "15:15",
    }
    result = score_item(good_action)
    assert result["score"] >= 50, f"Expected score >= 50, got {result['score']}"
    assert result["grade"] in ("actionable", "needs-context"), f"Expected good grade, got {result['grade']}"
    assert result["theme"], "Expected a theme to be assigned"

    # Vague idea should score lower
    vague_idea = {
        "tag": "IDEA",
        "text": "maybe something cool someday",
        "time": "14:30",
    }
    vague_result = score_item(vague_idea)
    assert vague_result["score"] < result["score"], \
        f"Vague ({vague_result['score']}) should score lower than specific ({result['score']})"

    print(f"  PASS — good action: {result['score']}/100 ({result['grade']}), "
          f"vague idea: {vague_result['score']}/100 ({vague_result['grade']})")
    print()


def test_triage_routing():
    """Test keyword-based theme routing."""
    print("TEST 15: Triage theme routing...")

    from triage import route_to_theme

    assert route_to_theme("Sensors use 3-tile detection radius") == "Game Systems"
    assert route_to_theme("Need 2 more map layouts for enemies") == "Content"
    assert route_to_theme("Visual pulse effect along signal paths") in ("Visual Polish", "Game Systems")
    assert route_to_theme("AXIS personality and dialogue flavor text") == "AXIS / Lore"
    assert route_to_theme("Refactor the service locator pattern") == "Architecture / Code"

    print("  PASS — all items routed to expected themes")
    print()


def test_triage_session():
    """Test triaging a full session."""
    print("TEST 16: Triage full session...")

    from triage import triage_session

    batches = parse_session_log(SAMPLE_LOG)
    flat_items = []
    for batch in batches:
        for item in batch["items"]:
            flat_items.append({**item, "time": batch["time"]})

    result = triage_session(flat_items)

    assert result["summary"]["total"] == 11
    assert result["summary"]["avg_score"] > 0
    assert len(result["results"]) == 11

    # Every result should have a grade
    for r in result["results"]:
        assert r["grade"] in ("actionable", "needs-context", "parked", "stale"), \
            f"Invalid grade: {r['grade']}"
        assert r["theme"], f"Missing theme for: {r['text']}"

    grades = result["summary"]["by_grade"]
    print(f"  PASS — 11 items triaged, avg score {result['summary']['avg_score']:.0f}/100")
    print(f"         grades: {grades}")
    print()


def test_triage_training():
    """Test adding custom training keywords."""
    print("TEST 17: Triage training...")

    from triage import train, load_training, save_training

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir=tempfile.gettempdir()) as f:
        train_path = f.name

    try:
        os.unlink(train_path)

        # Train with custom keywords
        result = train("Game Systems", ["flux_capacitor", "quantum_gate"], path=train_path)
        assert "flux_capacitor" in result["Game Systems"]
        assert "quantum_gate" in result["Game Systems"]
        # Original defaults should still be there
        assert "sensor" in result["Game Systems"]

        # Load back and verify persistence
        loaded = load_training(train_path)
        assert "flux_capacitor" in loaded["Game Systems"]

        print("  PASS — custom keywords added and persisted")
    finally:
        if os.path.exists(train_path):
            os.unlink(train_path)
    print()


def test_triage_analytics():
    """Test analytics recording."""
    print("TEST 18: Triage analytics...")

    from triage import TriageAnalytics

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, dir=tempfile.gettempdir()) as f:
        analytics_path = f.name

    try:
        os.unlink(analytics_path)
        analytics = TriageAnalytics(analytics_path)

        analytics.record_session({
            "total": 11,
            "by_grade": {"actionable": 5, "needs-context": 4, "parked": 2},
            "by_theme": {"Game Systems": 4, "Content": 3},
            "by_tag": {"DECISION": 3, "IDEA": 3, "ACTION": 3, "QUESTION": 1, "WATCH": 1},
            "avg_score": 67.5,
        })

        summary = analytics.get_summary()
        assert summary["total_processed"] == 11
        assert summary["session_count"] == 1
        assert summary["by_grade"]["actionable"] == 5
        assert 67.5 in summary["avg_score_trend"]

        print(f"  PASS — analytics recorded: {summary['total_processed']} items, "
              f"{summary['session_count']} session(s)")
    finally:
        if os.path.exists(analytics_path):
            os.unlink(analytics_path)
    print()


def main():
    print()
    print("=" * 50)
    print("Session Digest — Smoke Tests")
    print("=" * 50)
    print()

    test_parse_session_log()
    test_parse_empty_log()
    test_parse_nothing_to_report()
    test_extract_session_header()
    test_format_items_for_claude()
    test_format_session_block()
    test_write_digest_new()
    test_write_digest_append()
    test_tag_preservation()
    test_parse_themed_output()
    test_db_insert_and_search()
    test_db_index_from_themed()
    test_db_index_fallback_raw()
    test_triage_scoring()
    test_triage_routing()
    test_triage_session()
    test_triage_training()
    test_triage_analytics()

    print("=" * 50)
    print("All tests passed.")
    print("=" * 50)


if __name__ == "__main__":
    main()
