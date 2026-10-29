"""User config at ~/.config/repo2diagram/config.json (overridable with env vars).

Keys:
  provider   auto | ollama | openai | anthropic | none
  model      model name for the chosen provider
  base_url   endpoint for ollama / openai-compatible servers (LM Studio, vLLM, llama.cpp, OpenRouter …)
  api_key    key for openai-compatible servers or Anthropic
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

KEYS = ("provider", "model", "base_url", "api_key")
PROVIDERS = ("auto", "ollama", "openai", "anthropic", "none")
ENV = {
    "provider": "R2D_PROVIDER",
    "model": "R2D_MODEL",
    "base_url": "R2D_BASE_URL",
    "api_key": "R2D_API_KEY",
}


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "repo2diagram" / "config.json"


def load_file() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}
    return {k: v for k, v in data.items() if k in KEYS and v}


def save_file(data: dict) -> Path:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({k: v for k, v in data.items() if v}, indent=2) + "\n")
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)  # it may hold an API key
    return p


def set_value(key: str, value: str) -> Path:
    if key not in KEYS:
        raise ValueError(f"unknown key {key!r}; expected one of {', '.join(KEYS)}")
    if key == "provider" and value not in PROVIDERS:
        raise ValueError(f"provider must be one of {', '.join(PROVIDERS)}")
    data = load_file()
    data[key] = value
    return save_file(data)


def unset_value(key: str) -> Path:
    data = load_file()
    data.pop(key, None)
    return save_file(data)


def resolve(**overrides: str | None) -> dict:
    """File < env vars < explicit overrides (CLI flags / MCP args)."""
    cfg = {"provider": "auto"}
    cfg.update(load_file())
    for key, env in ENV.items():
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    cfg.update({k: v for k, v in overrides.items() if v})
    return cfg


def redacted(cfg: dict) -> dict:
    out = dict(cfg)
    if out.get("api_key"):
        k = out["api_key"]
        out["api_key"] = k[:6] + "…" + k[-4:] if len(k) > 12 else "…"
    return out
