"""Loads settings from a .env file and environment variables. No extra packages needed."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)  # real env vars win over .env


def get(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o",
    "grok": "grok-4.3",
    "kimi": "kimi-k2.6",
    "moonshot": "kimi-k2.6",
    "xai": "grok-4.3",
    "openrouter": "anthropic/claude-sonnet-5",
    "ollama": "qwen2.5:14b",
}
