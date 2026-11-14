"""Provider-agnostic LLM layer. The ONLY file that talks to an AI service.
Set LC_PROVIDER, LC_MODEL, LC_KEY (and LC_BASE_URL for 'custom') in .env.
"""
import json, os, re
import requests

# name -> (base_url, kind). 'openai' kind = OpenAI-compatible /chat/completions
PROVIDERS = {
    "openai":     ("https://api.openai.com/v1", "openai"),
    "gemini":     ("https://generativelanguage.googleapis.com/v1beta/openai", "openai"),
    "xai":        ("https://api.x.ai/v1", "openai"),
    "groq":       ("https://api.groq.com/openai/v1", "openai"),
    "openrouter": ("https://openrouter.ai/api/v1", "openai"),
    "ollama":     ("http://localhost:11434/v1", "openai"),
    "custom":     (None, "openai"),
    "anthropic":  ("https://api.anthropic.com/v1", "anthropic"),
}


def configured() -> bool:
    p = os.getenv("LC_PROVIDER", "").lower()
    return bool(p and os.getenv("LC_MODEL") and (os.getenv("LC_KEY") or p == "ollama"))


def _extract_json(text: str):
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group(0))


def _call(provider, model, system, user):
    base, kind = PROVIDERS[provider]
    base = os.getenv("LC_BASE_URL") or base
    key = os.getenv("LC_KEY", "")
    if kind == "anthropic":
        r = requests.post(
            f"{base}/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            json={"model": model, "max_tokens": 4096, "system": system,
                  "messages": [{"role": "user", "content": user}]},
            timeout=180)
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json()["content"])
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    body = {"model": model, "temperature": 0.3,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}]}
    r = requests.post(f"{base}/chat/completions", headers=headers,
                      json={**body, "response_format": {"type": "json_object"}}, timeout=180)
    if r.status_code == 400:  # some servers don't support response_format
        r = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=180)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def chat_json(system: str, user: str):
    provider = os.environ["LC_PROVIDER"].lower()
    if provider not in PROVIDERS:
        raise SystemExit(f"Unknown LC_PROVIDER '{provider}'. Options: {', '.join(PROVIDERS)}")
    last = None
    for model in [m.strip() for m in os.environ["LC_MODEL"].split(",") if m.strip()]:
        try:  # comma-separated models = fallback chain
            return _extract_json(_call(provider, model, system, user))
        except Exception as e:
            last = e
            print(f"  ! {model} failed: {e}")
    raise RuntimeError(f"All models failed: {last}")
