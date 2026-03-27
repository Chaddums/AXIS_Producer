"""Focus Advisor — cross-references incoming Slack/email messages against
the digest DB to surface what's relevant to current project priorities.

When a Slack message or email arrives, the advisor:
1. Searches the digest DB for related items (keyword overlap)
2. Checks triage scores of matching items
3. If high-priority matches exist, fires a notification callback

This helps the user know when an incoming message relates to something
they've already discussed or committed to in a voice session.
"""

import re
import threading
import time
from collections import deque
from datetime import datetime

from digest_db import DigestDB, DEFAULT_DB_PATH
from triage import route_to_theme, load_training, DEFAULT_THEME_KEYWORDS


# Priority thresholds
SCORE_THRESHOLD_HIGH = 70     # "actionable" items — always notify
SCORE_THRESHOLD_MEDIUM = 50   # "needs-context" items — notify if keyword match strong
MIN_KEYWORD_HITS = 2          # minimum keyword overlaps to consider a match
DEDUP_WINDOW_SEC = 300        # don't re-notify same topic within 5 minutes


class FocusMatch:
    """A match between an incoming message and a digest DB item."""
    __slots__ = ("source", "sender", "message_preview", "matched_item",
                 "matched_tag", "matched_theme", "triage_score", "triage_grade",
                 "keyword_hits", "timestamp")

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __repr__(self):
        return (f"FocusMatch(source={self.source!r}, score={self.triage_score}, "
                f"grade={self.triage_grade!r}, hits={self.keyword_hits})")

    @property
    def priority(self) -> str:
        """Human-readable priority level."""
        if self.triage_score >= SCORE_THRESHOLD_HIGH:
            return "HIGH"
        elif self.triage_score >= SCORE_THRESHOLD_MEDIUM:
            return "MEDIUM"
        return "LOW"

    def format_notification(self) -> str:
        """Format for display in tray notification or log."""
        return (
            f"[{self.priority}] {self.source} — {self.sender}\n"
            f"  Message: {self.message_preview}\n"
            f"  Matches: [{self.matched_tag}] {self.matched_item}\n"
            f"  Theme: {self.matched_theme} | Score: {self.triage_score}/100 ({self.triage_grade})"
        )


class FocusAdvisor:
    """Cross-references incoming messages against the digest DB.

    on_focus_match(match: FocusMatch) — called when a high-priority match is found.
    """

    def __init__(self, on_focus_match=None,
                 db_path: str = DEFAULT_DB_PATH,
                 verbose: bool = False):
        self.on_focus_match = on_focus_match
        self.db_path = db_path
        self.verbose = verbose

        self._lock = threading.Lock()
        self._recent_topics: deque[tuple[float, str]] = deque(maxlen=50)
        self._theme_keywords = load_training()
        self._match_count = 0
        self._check_count = 0

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from a message for DB searching."""
        # Lowercase, strip punctuation, split
        clean = re.sub(r'[^\w\s]', ' ', text.lower())
        words = clean.split()

        # Filter out stop words and very short words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "can", "shall",
            "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "about", "between", "through", "during", "before",
            "after", "above", "below", "up", "down", "out", "off", "over",
            "under", "again", "further", "then", "once", "here", "there",
            "when", "where", "why", "how", "all", "each", "every", "both",
            "few", "more", "most", "other", "some", "such", "no", "nor",
            "not", "only", "own", "same", "so", "than", "too", "very",
            "just", "but", "and", "or", "if", "while", "this", "that",
            "these", "those", "it", "its", "i", "me", "my", "we", "our",
            "you", "your", "he", "him", "his", "she", "her", "they", "them",
            "what", "which", "who", "whom",
            "hi", "hey", "hello", "thanks", "thank", "please", "ok", "okay",
            "yes", "no", "yeah", "sure", "got", "get", "let", "know",
        }

        return [w for w in words if len(w) >= 3 and w not in stop_words]

    def _is_duplicate_topic(self, topic_key: str) -> bool:
        """Check if we've already notified about this topic recently."""
        now = time.time()
        with self._lock:
            # Clean old entries
            while self._recent_topics and now - self._recent_topics[0][0] > DEDUP_WINDOW_SEC:
                self._recent_topics.popleft()

            for ts, key in self._recent_topics:
                if key == topic_key:
                    return True

            self._recent_topics.append((now, topic_key))
            return False

    def check_message(self, source: str, sender: str, text: str,
                      timestamp: str = "") -> FocusMatch | None:
        """Check an incoming message against the digest DB.

        Returns a FocusMatch if a high-priority match is found, else None.
        """
        self._check_count += 1
        keywords = self._extract_keywords(text)
        if len(keywords) < 2:
            return None

        # Build search queries from keyword combinations
        # Try the full keyword set first, then pairs of strongest keywords
        db = DigestDB(self.db_path)
        best_match = None
        best_score = 0
        best_hits = 0

        try:
            # Strategy 1: search with all keywords joined
            query = " ".join(keywords[:8])  # limit to 8 keywords
            results = db.search(query, limit=10)

            for r in results:
                score = r.get("triage_score", 0)
                grade = r.get("triage_grade", "")

                # Count how many of our keywords appear in the matched item
                item_lower = r["text"].lower()
                hits = sum(1 for kw in keywords if kw in item_lower)

                if hits < MIN_KEYWORD_HITS:
                    continue

                # Weight: triage_score * keyword_hits
                weighted = score * hits
                if weighted > best_score:
                    best_score = weighted
                    best_hits = hits
                    best_match = r

            # Strategy 2: search individual high-signal keywords
            if best_match is None:
                # Identify keywords that match theme routing (project-specific terms)
                theme_words = set()
                for kws in self._theme_keywords.values():
                    theme_words.update(kw.lower() for kw in kws)

                project_keywords = [kw for kw in keywords if kw in theme_words]
                for pk in project_keywords[:3]:
                    results = db.search(pk, limit=5)
                    for r in results:
                        score = r.get("triage_score", 0)
                        if score >= SCORE_THRESHOLD_MEDIUM:
                            item_lower = r["text"].lower()
                            hits = sum(1 for kw in keywords if kw in item_lower)
                            if hits >= 1:
                                weighted = score * max(hits, 2)
                                if weighted > best_score:
                                    best_score = weighted
                                    best_hits = max(hits, 2)
                                    best_match = r
        finally:
            db.close()

        if best_match is None:
            return None

        match_score = best_match.get("triage_score", 0)
        match_grade = best_match.get("triage_grade", "")

        # Only notify for meaningful matches
        if match_score < SCORE_THRESHOLD_MEDIUM:
            return None
        if best_hits < MIN_KEYWORD_HITS and match_score < SCORE_THRESHOLD_HIGH:
            return None

        # Deduplicate
        topic_key = best_match["text"][:40].lower()
        if self._is_duplicate_topic(topic_key):
            return None

        message_preview = text[:120] + ("..." if len(text) > 120 else "")

        match = FocusMatch(
            source=source,
            sender=sender,
            message_preview=message_preview,
            matched_item=best_match["text"],
            matched_tag=best_match.get("tag", ""),
            matched_theme=best_match.get("theme", ""),
            triage_score=match_score,
            triage_grade=match_grade,
            keyword_hits=best_hits,
            timestamp=timestamp or datetime.now().strftime("%H:%M"),
        )

        self._match_count += 1

        if self.verbose:
            print(f"  [focus] {match}")

        if self.on_focus_match:
            self.on_focus_match(match)

        return match

    @property
    def stats(self) -> dict:
        return {
            "messages_checked": self._check_count,
            "matches_found": self._match_count,
        }
