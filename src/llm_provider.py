"""LLM Provider — unified interface for multiple AI backends.

Supports: Ollama (local), Anthropic, OpenAI, Google Gemini, Groq.
Default is Ollama (fully private, no data leaves the machine).
BYOK: user provides their own API key for any cloud provider.
AXIS hosted: uses AXIS backend proxy with metered Anthropic key.
"""

import json
import logging
import os

log = logging.getLogger(__name__)

# Default models per provider
DEFAULT_MODELS = {
    "ollama": "llama3.1:8b",
    "anthropic": "claude-sonnet-4-20250514",
    "openai": "gpt-4o-mini",
    "google": "gemini-2.0-flash",
    "groq": "llama-3.1-70b-versatile",
}

PROVIDER_NAMES = {
    "ollama": "Ollama (Local)",
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI (GPT)",
    "google": "Google (Gemini)",
    "groq": "Groq (Fast Cloud)",
}


def call_llm(provider: str, system: str, user_message: str,
             api_key: str = "", model: str = "",
             max_tokens: int = 1024,
             ollama_url: str = "http://localhost:11434") -> str:
    """Send a prompt to the configured LLM and return the response text.

    Args:
        provider: ollama | anthropic | openai | google | groq
        system: system prompt
        user_message: user message (transcript)
        api_key: API key (not needed for ollama)
        model: model name override (uses default if empty)
        max_tokens: max response tokens
        ollama_url: Ollama server URL (local)

    Returns:
        Response text from the LLM.

    Raises:
        RuntimeError on failure.
    """
    model = model or DEFAULT_MODELS.get(provider, "")

    if provider == "ollama":
        return _call_ollama(system, user_message, model, ollama_url, max_tokens)
    elif provider == "anthropic":
        return _call_anthropic(system, user_message, api_key, model, max_tokens)
    elif provider == "openai":
        return _call_openai(system, user_message, api_key, model, max_tokens)
    elif provider == "google":
        return _call_google(system, user_message, api_key, model, max_tokens)
    elif provider == "groq":
        return _call_groq(system, user_message, api_key, model, max_tokens)
    else:
        raise RuntimeError(f"Unknown LLM provider: {provider}")


def _call_ollama(system: str, user_message: str, model: str,
                 base_url: str, max_tokens: int) -> str:
    """Call Ollama local server."""
    import httpx
    try:
        resp = httpx.post(
            f"{base_url}/api/chat",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
                "options": {"num_predict": max_tokens},
            },
            timeout=120.0,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Ollama error {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        return data.get("message", {}).get("content", "")
    except httpx.ConnectError:
        raise RuntimeError(
            "Ollama not running. Install from https://ollama.com and run: ollama serve"
        )


def _call_anthropic(system: str, user_message: str, api_key: str,
                    model: str, max_tokens: int) -> str:
    """Call Anthropic Claude API."""
    import anthropic
    key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise RuntimeError("Anthropic API key not set")
    client = anthropic.Anthropic(api_key=key)
    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def _call_openai(system: str, user_message: str, api_key: str,
                 model: str, max_tokens: int) -> str:
    """Call OpenAI API (or compatible endpoint)."""
    import httpx
    key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise RuntimeError("OpenAI API key not set")
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        },
        timeout=120.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OpenAI error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _call_google(system: str, user_message: str, api_key: str,
                 model: str, max_tokens: int) -> str:
    """Call Google Gemini API."""
    import httpx
    key = api_key or os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        raise RuntimeError("Google API key not set")
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": key},
        headers={"Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": user_message}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        },
        timeout=120.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Gemini error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError("Gemini returned no candidates")
    parts = candidates[0].get("content", {}).get("parts", [])
    return parts[0].get("text", "") if parts else ""


def _call_groq(system: str, user_message: str, api_key: str,
               model: str, max_tokens: int) -> str:
    """Call Groq API (OpenAI-compatible)."""
    import httpx
    key = api_key or os.environ.get("GROQ_API_KEY", "")
    if not key:
        raise RuntimeError("Groq API key not set")
    resp = httpx.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        },
        timeout=120.0,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Groq error {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def check_provider(provider: str, api_key: str = "",
                   ollama_url: str = "http://localhost:11434") -> dict:
    """Quick health check for a provider. Returns {ok: bool, error: str}."""
    try:
        if provider == "ollama":
            import httpx
            r = httpx.get(f"{ollama_url}/api/tags", timeout=5.0)
            if r.status_code != 200:
                return {"ok": False, "error": "Ollama not responding"}
            models = [m["name"] for m in r.json().get("models", [])]
            if not models:
                return {"ok": False, "error": "No models installed. Run: ollama pull llama3.1:8b"}
            return {"ok": True, "models": models}
        elif provider == "anthropic":
            key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
            if not key:
                return {"ok": False, "error": "No API key"}
            return {"ok": True}
        elif provider == "openai":
            key = api_key or os.environ.get("OPENAI_API_KEY", "")
            if not key:
                return {"ok": False, "error": "No API key"}
            return {"ok": True}
        elif provider == "google":
            key = api_key or os.environ.get("GOOGLE_API_KEY", "")
            if not key:
                return {"ok": False, "error": "No API key"}
            return {"ok": True}
        elif provider == "groq":
            key = api_key or os.environ.get("GROQ_API_KEY", "")
            if not key:
                return {"ok": False, "error": "No API key"}
            return {"ok": True}
        else:
            return {"ok": False, "error": f"Unknown provider: {provider}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
