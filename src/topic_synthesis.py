"""Topic Synthesis — clusters events by topic across people, scores relevance.

Takes raw events and produces:
1. Topic clusters: groups of related items across multiple people
2. People map: who is working on what, grouped by overlap
3. Relevance scores: what matters to the viewer based on their work areas
4. Action items: blockers, decisions, conflicts that need attention
"""

from collections import defaultdict
from llm_provider import call_llm, DEFAULT_MODELS


CLUSTER_PROMPT = """\
You are analyzing a team's recent session data. Multiple people recorded sessions \
and AXIS extracted structured items from each.

Your job: synthesize this into a team briefing. Group related items by TOPIC \
(not by person). Multiple people may have discussed the same topic.

Input: a list of session batch summaries with who said what.

Output this exact JSON structure (no markdown fences, just raw JSON):
{
  "topics": [
    {
      "title": "short topic name (3-6 words)",
      "summary": "1-2 sentence synthesis of what the team discussed/decided about this",
      "priority": "critical|warning|info|ambient",
      "people": ["who1", "who2"],
      "decisions": ["any decisions made"],
      "blockers": ["any blockers identified"],
      "actions": ["any action items"],
      "questions": ["any open questions"]
    }
  ],
  "needs_action": [
    {
      "what": "specific thing needing attention",
      "why": "brief reason",
      "who": ["relevant people"],
      "priority": "critical|warning|info"
    }
  ],
  "conflicts": [
    {
      "description": "what conflicts or overlaps",
      "between": ["person1", "person2"],
      "priority": "warning|critical"
    }
  ]
}

Rules:
- Merge related items across people into single topics
- A topic discussed by 3 people is MORE important than one discussed by 1
- Blockers and unresolved questions go in needs_action
- If two people are working on the same thing differently, flag as conflict
- Sort topics by priority (critical first) then by number of people involved
- Keep it tight — max 5 topics, max 3 needs_action, max 2 conflicts
- Keep each summary under 30 words
- Max 3 items per decisions/blockers/actions/questions array
- If nothing fits a section, return empty array
"""

RELEVANCE_PROMPT = """\
Given a team briefing with topics, and the viewer's identity/work areas below, \
score each topic's relevance to the viewer from 0-100.

Viewer: {viewer_identity}
Viewer's recent work areas: {viewer_areas}

Return JSON array of objects: [{"topic": "topic title", "relevance": 0-100, "reason": "why"}]
Only include topics scoring 30+. Sort by relevance descending.
"""


def synthesize_events(events: list[dict], provider: str = "anthropic",
                      api_key: str = "", model: str = "",
                      ollama_url: str = "http://localhost:11434") -> dict:
    """Cluster events by topic across people. Returns briefing structure."""
    # Group items by person from session_batch events
    person_items = defaultdict(list)
    all_items = []

    for e in events:
        who = e.get("who", "unknown")
        raw = e.get("raw", {})
        items = raw.get("items", [])
        summary = e.get("summary", "")

        if e.get("event_type") == "session_batch" and items:
            for item in items:
                entry = {
                    "who": who,
                    "category": item.get("category", ""),
                    "text": item.get("text", ""),
                }
                person_items[who].append(entry)
                all_items.append(entry)
        elif e.get("stream") == "chat" and summary:
            entry = {"who": who, "category": "Chat", "text": summary}
            person_items[who].append(entry)
            all_items.append(entry)

    if not all_items:
        return {"topics": [], "needs_action": [], "conflicts": [], "people": {}}

    # Format for LLM — cap items per person to stay within token limits
    MAX_ITEMS_PER_PERSON = 15
    lines = []
    for who, items in person_items.items():
        lines.append(f"\n--- {who} ---")
        # Prioritize blockers/actions/decisions over discussion
        priority_order = {"Blockers": 0, "Action Items": 1, "Decisions Locked": 2,
                          "Watch List": 3, "Open Questions": 4, "Ideas Generated": 5,
                          "Key Discussion": 6, "Chat": 7}
        sorted_items = sorted(items, key=lambda i: priority_order.get(i["category"], 99))
        for item in sorted_items[:MAX_ITEMS_PER_PERSON]:
            lines.append(f"  [{item['category']}] {item['text'][:120]}")

    input_text = "\n".join(lines)

    # Ask LLM to cluster
    try:
        response = call_llm(
            provider=provider,
            system=CLUSTER_PROMPT,
            user_message=input_text,
            api_key=api_key,
            model=model,
            max_tokens=4096,
            ollama_url=ollama_url,
        )
        import json
        # Strip markdown fences if present
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
        briefing = json.loads(clean)
    except Exception as e:
        return {
            "topics": [],
            "needs_action": [],
            "conflicts": [],
            "people": dict(person_items),
            "error": str(e),
        }

    # Add people map
    people_topics = defaultdict(list)
    for topic in briefing.get("topics", []):
        for person in topic.get("people", []):
            people_topics[person].append(topic.get("title", ""))

    briefing["people"] = {
        who: {
            "item_count": len(items),
            "topics": people_topics.get(who, []),
        }
        for who, items in person_items.items()
    }

    return briefing


def score_relevance(briefing: dict, viewer_identity: str,
                    viewer_areas: list[str],
                    provider: str = "anthropic", api_key: str = "",
                    model: str = "", ollama_url: str = "http://localhost:11434") -> list[dict]:
    """Score each topic's relevance to the viewer."""
    topics = briefing.get("topics", [])
    if not topics or not viewer_identity:
        return []

    topic_summaries = "\n".join(
        f"- {t['title']}: {t.get('summary', '')}" for t in topics
    )

    prompt = RELEVANCE_PROMPT.format(
        viewer_identity=viewer_identity,
        viewer_areas=", ".join(viewer_areas) if viewer_areas else "unknown",
    )

    try:
        response = call_llm(
            provider=provider,
            system=prompt,
            user_message=topic_summaries,
            api_key=api_key,
            model=model,
            max_tokens=512,
            ollama_url=ollama_url,
        )
        import json
        clean = response.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
        return json.loads(clean)
    except Exception:
        return []
