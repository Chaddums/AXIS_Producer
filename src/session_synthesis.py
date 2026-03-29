"""Session Synthesis — deep post-session analysis of communication dynamics.

Runs at session end. Takes the full session transcript and produces a
comprehensive report analyzing not just WHAT was said, but HOW people
interacted — where they agreed, disagreed, talked past each other, and why.

This is the core differentiator. Nobody else does this.
"""

import os
from datetime import datetime

from llm_provider import call_llm, DEFAULT_MODELS


SYNTHESIS_PROMPT = """\
You are an expert communication analyst and meeting facilitator reviewing \
a complete session transcript. Multiple people were talking — your job is to \
analyze not just the content, but the DYNAMICS of the conversation.

Produce a structured session report with these sections:

## Executive Summary
2-3 paragraphs: what this session was about, what was accomplished, \
and the overall dynamic between participants. Be specific about who \
said what and what the outcomes were.

## Key Outcomes
- Decisions made (who decided, what was decided)
- Commitments (who committed to what, by when if mentioned)
- Completed discussions (topics that were fully resolved)

## Points of Agreement
- Where participants aligned naturally
- Shared assumptions or values that emerged
- Consensus positions (note if consensus was genuine or someone just gave in)

## Points of Tension
- Specific disagreements (quote or paraphrase what each side said)
- Where people talked past each other (same words, different meanings)
- Unspoken assumptions that created friction
- Moments where someone's concern was dismissed or not heard
- For each tension point: WHY it happened (different priorities? \
  different context? ego? misunderstanding?)

## Communication Patterns
- Who drove the conversation vs who was reactive
- Who changed their position and why
- Recurring themes or circular arguments
- Productive patterns (what worked well in the discussion)
- Unproductive patterns (what kept them stuck)
- Power dynamics (who deferred to whom, why)

## Unresolved
- Topics that were raised but not resolved
- Questions that were asked but not answered
- Decisions that were deferred
- Elephants in the room (things implied but not said directly)

## Recommendations
- Specific follow-up actions (not generic "have a meeting")
- For each tension point: what would help resolve it
- Communication adjustments that could help future sessions
- Topics that need a dedicated discussion

Rules:
- Be DIRECT. Name people. Quote what they said when relevant.
- Don't sugarcoat tension — identify it clearly and explain why it happened.
- The value is in the analysis, not the summary. Anyone can summarize.
- If there's only one speaker, focus on their reasoning patterns, \
  contradictions, and areas where their thinking evolved.
- If the transcript is too short or unintelligible, say so honestly.
"""

# Lighter prompt for single-person sessions or shorter meetings
SOLO_SYNTHESIS_PROMPT = """\
You are reviewing a session transcript from a single person (or a person \
working with an AI assistant). Analyze their thinking process.

Produce a structured report:

## Executive Summary
What this session covered and what was accomplished.

## Key Outcomes
- Decisions made
- Action items identified
- Problems solved or approaches decided

## Thinking Patterns
- How their thinking evolved during the session
- Points where they changed direction and why
- Assumptions they're operating under (stated or implied)
- Blind spots — things they didn't consider

## Unresolved
- Open questions
- Deferred decisions
- Areas that need more thought

## Recommendations
- What to tackle next based on this session
- Potential issues to watch for
- Things to validate before committing

Be specific, reference what was actually said. No fluff.
"""


def generate_session_report(transcript: str, session_metadata: dict = None,
                            provider: str = "anthropic", api_key: str = "",
                            model: str = "", ollama_url: str = "http://localhost:11434",
                            max_tokens: int = 4096) -> str:
    """Generate a deep session synthesis report from the full transcript.

    Args:
        transcript: The complete session transcript text
        session_metadata: Optional dict with session info (duration, participants, etc.)
        provider: LLM provider
        api_key: API key
        model: Model override
        ollama_url: Ollama URL for local

    Returns:
        Markdown-formatted session report
    """
    if not transcript or len(transcript.split()) < 50:
        return "# Session Report\n\nInsufficient transcript data for analysis."

    # Detect if this is multi-person or solo
    # Look for speaker labels like [HH:MM] or different names
    lines = transcript.split("\n")
    speakers = set()
    for line in lines:
        # Common patterns: "[12:30] Speaker:" or "Speaker:" at line start
        if "]" in line[:20]:
            rest = line.split("]", 1)[1].strip() if "]" in line else line
            if ":" in rest[:40]:
                speaker = rest.split(":")[0].strip()
                if speaker and len(speaker) < 30:
                    speakers.add(speaker.lower())

    is_multi = len(speakers) > 1
    prompt = SYNTHESIS_PROMPT if is_multi else SOLO_SYNTHESIS_PROMPT

    # Add session context if available
    context_lines = []
    if session_metadata:
        if session_metadata.get("duration"):
            context_lines.append(f"Session duration: {session_metadata['duration']}")
        if session_metadata.get("participants"):
            context_lines.append(f"Participants: {', '.join(session_metadata['participants'])}")
        if session_metadata.get("topic"):
            context_lines.append(f"Topic: {session_metadata['topic']}")

    user_message = transcript
    if context_lines:
        user_message = "\n".join(context_lines) + "\n\n---\n\n" + transcript

    # Truncate if too long (keep first and last portions for context)
    words = user_message.split()
    MAX_WORDS = 15000  # ~20k tokens, leaves room for response
    if len(words) > MAX_WORDS:
        half = MAX_WORDS // 2
        user_message = " ".join(words[:half]) + \
            "\n\n[... middle of session omitted for length ...]\n\n" + \
            " ".join(words[-half:])

    report = call_llm(
        provider=provider,
        system=prompt,
        user_message=user_message,
        api_key=api_key,
        model=model or DEFAULT_MODELS.get(provider, ""),
        max_tokens=max_tokens,
        ollama_url=ollama_url,
    )

    # Add header
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"# AXIS Session Report\nGenerated: {now}\n"
    if session_metadata:
        if session_metadata.get("duration"):
            header += f"Duration: {session_metadata['duration']}\n"
        if session_metadata.get("participants"):
            header += f"Participants: {', '.join(session_metadata['participants'])}\n"
    header += "\n---\n\n"

    footer = "\n\n---\n\n> *AI-generated analysis. Not a verbatim transcript. " \
             "Verify important points independently.*\n"

    return header + report + footer


def save_session_report(report: str, output_dir: str = ".",
                        filename: str = None) -> str:
    """Save the session report to a markdown file.

    Returns the file path.
    """
    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"session_report_{timestamp}.md"

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)

    return path
