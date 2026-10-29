"""Tiny provider layer: local Ollama, any OpenAI-compatible server, or Claude.

Each client exposes ``complete(system, prompt) -> str``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

OLLAMA_URL = "http://localhost:11434"
OLLAMA_PREFERRED = ("qwen2.5-coder", "qwen3", "llama3.1", "llama3.2", "gemma3", "mistral", "phi4")
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"


class LLMError(RuntimeError):
    pass


@dataclass
class OllamaClient:
    model: str
    base_url: str = OLLAMA_URL
    name: str = "ollama"

    def complete(self, system: str, prompt: str) -> str:
        try:
            r = httpx.post(
                f"{self.base_url.rstrip('/')}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "options": {"temperature": 0.1, "num_ctx": 8192},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                },
                timeout=300,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"Ollama request failed: {e}") from e
        return r.json().get("message", {}).get("content", "")


@dataclass
class OpenAICompatClient:
    model: str
    base_url: str
    api_key: str = ""
    name: str = "openai"

    def complete(self, system: str, prompt: str) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            r = httpx.post(
                f"{self.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "temperature": 0.1,
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
                },
                timeout=300,
            )
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise LLMError(f"OpenAI-compatible request failed: {e}") from e
        return r.json()["choices"][0]["message"]["content"]


@dataclass
class ClaudeClient:
    model: str = DEFAULT_CLAUDE_MODEL
    api_key: str = ""
    name: str = "anthropic"

    def __post_init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        # No explicit key -> the SDK resolves ANTHROPIC_API_KEY / `ant auth login` profiles itself.
        self._client = anthropic.Anthropic(api_key=self.api_key) if self.api_key else anthropic.Anthropic()

    def complete(self, system: str, prompt: str) -> str:
        a = self._anthropic
        try:
            # Summaries are short, simple work, so effort stays low. On a safety decline the
            # server re-runs the request on a fallback model.
            response = self._client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={"effort": "low"},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as e:
            raise LLMError("Claude rejected the API key — run `repo2diagram config set api_key ...`") from e
        except a.RateLimitError as e:
            raise LLMError("Claude rate limit hit; try again shortly") from e
        except a.APIStatusError as e:
            raise LLMError(f"Claude API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            raise LLMError("Could not reach the Claude API") from e
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined to summarize this module")
        return "".join(b.text for b in response.content if b.type == "text")


def ollama_url(base_url: str | None = None) -> str:
    url = base_url or os.environ.get("OLLAMA_HOST") or OLLAMA_URL
    return url if url.startswith("http") else f"http://{url}"


def ollama_models(base_url: str = OLLAMA_URL) -> list[str] | None:
    """Installed Ollama models, or None if Ollama isn't running."""
    try:
        r = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=2)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    return [m["name"] for m in r.json().get("models", [])]


def pick_ollama_model(models: list[str]) -> str | None:
    for pref in OLLAMA_PREFERRED:
        for m in models:
            if m.startswith(pref):
                return m
    return models[0] if models else None


def make_client(cfg: dict):
    """Build a client from a resolved config, or return None for static-only mode."""
    provider = cfg.get("provider", "auto")
    model = cfg.get("model")
    base_url = cfg.get("base_url")
    api_key = cfg.get("api_key", "")

    if provider == "none":
        return None
    if provider == "anthropic":
        return ClaudeClient(model=model or DEFAULT_CLAUDE_MODEL, api_key=api_key)
    if provider == "openai":
        return OpenAICompatClient(
            model=model or DEFAULT_OPENAI_MODEL,
            base_url=base_url or "https://api.openai.com/v1",
            api_key=api_key or os.environ.get("OPENAI_API_KEY", ""),
        )
    if provider in ("ollama", "auto"):
        url = ollama_url(base_url)
        models = ollama_models(url)
        if models:
            chosen = model if model and any(m == model or m.startswith(model + ":") for m in models) else None
            chosen = chosen or pick_ollama_model(models)
            return OllamaClient(model=chosen, base_url=url)
        if provider == "ollama":
            raise LLMError(f"Ollama isn't reachable at {url} (or has no models). Try `ollama pull qwen2.5-coder:7b`.")
        if os.environ.get("ANTHROPIC_API_KEY") or (api_key and api_key.startswith("sk-ant")):
            return ClaudeClient(model=model or DEFAULT_CLAUDE_MODEL, api_key=api_key)
        return None
    raise LLMError(f"unknown provider {provider!r}")
