"""Provider-agnostic LLM layer. The ONLY file that talks to an AI service.
Configure LC_PROVIDER, LC_MODEL, and LC_KEY (and LC_BASE_URL for 'custom') in .env.
"""
import json
import os
import re
import requests

# Provider registry: name -> (base_url, kind, default_models)
PROVIDERS = {
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "gemini",
        "gemini-1.5-flash,gemini-2.0-flash,gemini-1.5-pro",
    ),
    "openai": (
        "https://api.openai.com/v1",
        "openai",
        "gpt-4o-mini,gpt-4o",
    ),
    "groq": (
        "https://api.groq.com/openai/v1",
        "openai",
        "llama-3.3-70b-versatile,llama-3.1-8b-instant",
    ),
    "anthropic": (
        "https://api.anthropic.com/v1",
        "anthropic",
        "claude-3-5-haiku-20241022,claude-3-5-sonnet-20241022",
    ),
    "deepseek": (
        "https://api.deepseek.com/v1",
        "openai",
        "deepseek-chat",
    ),
    "xai": (
        "https://api.x.ai/v1",
        "openai",
        "grok-2-latest",
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1",
        "openai",
        "google/gemini-flash-1.5,meta-llama/llama-3.3-70b-instruct",
    ),
    "ollama": (
        "http://localhost:11434/v1",
        "openai",
        "llama3.2,mistral",
    ),
    "custom": (
        None,
        "openai",
        "default",
    ),
}


def configured() -> bool:
    """Check if AI is configured and ready."""
    provider = os.getenv("LC_PROVIDER", "").lower().strip()
    if not provider or provider not in PROVIDERS:
        return False
    key = os.getenv("LC_KEY", "").strip()
    # Ollama is local, doesn't require key
    if provider == "ollama":
        return True
    return bool(key)


def get_default_models(provider: str) -> str:
    """Return default models for provider if LC_MODEL not specified."""
    if provider in PROVIDERS:
        return PROVIDERS[provider][2]
    return "default"


def _extract_json(text: str):
    """Extract valid JSON from LLM response with robust fallback."""
    text = text.strip()
    # Strip markdown code blocks
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Regex search for outer JSON object
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise ValueError(f"Could not parse valid JSON from AI response: {text[:300]}")
        return json.loads(m.group(0))


def _call(provider: str, model: str, system: str, user: str):
    base, kind, _ = PROVIDERS[provider]
    base = (os.getenv("LC_BASE_URL") or base or "").rstrip("/")
    key = os.getenv("LC_KEY", "").strip()

    if not base and kind != "anthropic":
        raise ValueError(f"Provider '{provider}' requires LC_BASE_URL to be set.")

    # 1. Anthropic native endpoint
    if kind == "anthropic":
        headers = {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": model,
            "max_tokens": 4096,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": 0.3,
        }
        r = requests.post(f"{base}/messages", headers=headers, json=payload, timeout=180)
        if not r.ok:
            raise RuntimeError(f"Anthropic error ({r.status_code}): {r.text}")
        data = r.json()
        return "".join(b.get("text", "") for b in data.get("content", []))

    # 2. Gemini via OpenAI-compatible endpoint
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        if kind == "gemini":
            headers["x-goog-api-key"] = key

    body = {
        "model": model,
        "temperature": 0.3,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }

    # Attempt json_object response format
    try:
        r = requests.post(
            f"{base}/chat/completions",
            headers=headers,
            json={**body, "response_format": {"type": "json_object"}},
            timeout=180,
        )
        if r.status_code == 400 or r.status_code == 422:
            # Fall back without response_format if provider doesn't support it
            r = requests.post(
                f"{base}/chat/completions",
                headers=headers,
                json=body,
                timeout=180,
            )
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"Connection to {provider} failed: {e}")

    if not r.ok:
        raise RuntimeError(f"{provider} returned error {r.status_code}: {r.text}")

    resp_json = r.json()
    choices = resp_json.get("choices", [])
    if not choices:
        raise RuntimeError(f"No choices in {provider} response: {resp_json}")

    return choices[0]["message"]["content"]


def chat_json(system: str, user: str):
    """Execute LLM call with fallback model chain."""
    provider = os.getenv("LC_PROVIDER", "gemini").lower().strip()
    if provider not in PROVIDERS:
        options = ", ".join(PROVIDERS.keys())
        raise ValueError(f"Unknown LC_PROVIDER '{provider}'. Available options: {options}")

    model_spec = os.getenv("LC_MODEL", "").strip() or get_default_models(provider)
    models = [m.strip() for m in model_spec.split(",") if m.strip()]

    last_err = None
    for model in models:
        try:
            raw = _call(provider, model, system, user)
            return _extract_json(raw)
        except Exception as e:
            last_err = e
            print(f"  ! Model '{model}' failed: {e}")

    raise RuntimeError(f"All models for provider '{provider}' failed. Last error: {last_err}")
