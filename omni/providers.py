"""
Talks to language models. The agent keeps the conversation in one neutral format:

  {"role": "user", "content": "..."}
  {"role": "assistant", "content": "...", "tool_calls": [{"id", "name", "input"}]}
  {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}

Each provider translates that into its own API format, so swapping models is one flag.
"""
import json
import time

import requests

from . import config


class ProviderError(Exception):
    pass


def _post(url, headers, payload, retries=4):
    for attempt in range(retries):
        try:
            r = requests.post(url, headers=headers, json=payload,
                              timeout=float(config.get("OMNI_REQUEST_TIMEOUT", "180")))
        except requests.RequestException as e:
            if attempt == retries - 1:
                raise ProviderError(f"Network error: {e}")
            time.sleep(2 ** attempt)
            continue
        if r.status_code in (429, 500, 502, 503, 529):  # rate-limited / overloaded: back off
            time.sleep(2 ** attempt * 2)
            continue
        if r.status_code >= 400:
            raise ProviderError(f"HTTP {r.status_code}: {r.text[:800]}")
        return r.json()
    raise ProviderError("Gave up after repeated rate-limit/overload errors.")


class AnthropicProvider:
    def __init__(self, model):
        self.model = model
        self.key = config.get("ANTHROPIC_API_KEY")
        if not self.key:
            raise ProviderError("ANTHROPIC_API_KEY is missing. Put it in your .env file.")

    def _convert(self, messages):
        out = []
        for m in messages:
            if m["role"] == "user":
                # Merge into a preceding tool-result message so user/assistant turns keep alternating.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append({"type": "text", "text": m["content"]})
                else:
                    out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = []
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["input"]})
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "."}]})
            elif m["role"] == "tool":
                block = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                # Consecutive tool results must share one user message.
                if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                    out[-1]["content"].append(block)
                else:
                    out.append({"role": "user", "content": [block]})
        return out

    def chat(self, system, messages, tools):
        payload = {
            "model": self.model,
            "max_tokens": 8000,
            "system": system,
            "messages": self._convert(messages),
        }
        if tools:
            payload["tools"] = [{"name": t["name"], "description": t["description"], "input_schema": t["schema"]}
                                for t in tools]
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        data = _post("https://api.anthropic.com/v1/messages", headers, payload)
        text, calls = [], []
        for b in data.get("content", []):
            if b["type"] == "text":
                text.append(b["text"])
            elif b["type"] == "tool_use":
                calls.append({"id": b["id"], "name": b["name"], "input": b.get("input", {})})
        return {"content": "\n".join(text), "tool_calls": calls}


class OpenAICompatProvider:
    """Works with OpenAI, OpenRouter, Ollama, LM Studio, Groq, Together, DeepSeek... anything OpenAI-shaped."""

    def __init__(self, model, base_url, key_env):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.key = config.get(key_env) if key_env else "not-needed"
        if not self.key:
            raise ProviderError(f"{key_env} is missing. Put it in your .env file.")

    def _convert(self, system, messages):
        out = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "assistant":
                msg = {"role": "assistant", "content": m.get("content") or None}
                if m.get("reasoning"):
                    msg["reasoning_content"] = m["reasoning"]
                if m.get("tool_calls"):
                    msg["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"], "arguments": json.dumps(tc["input"])}}
                        for tc in m["tool_calls"]
                    ]
                out.append(msg)
            elif m["role"] == "tool":
                out.append({"role": "tool", "tool_call_id": m["tool_call_id"], "content": m["content"]})
            else:
                out.append({"role": "user", "content": m["content"]})
        return out

    def chat(self, system, messages, tools):
        payload = {
            "model": self.model,
            "messages": self._convert(system, messages),
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t["name"], "description": t["description"], "parameters": t["schema"]}} for t in tools]
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        data = _post(f"{self.base_url}/chat/completions", headers, payload)
        msg = data["choices"][0]["message"]
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc["function"].get("arguments")}
            calls.append({"id": tc.get("id") or f"call_{i}", "name": tc["function"]["name"], "input": args})
        return {"content": msg.get("content") or "", "tool_calls": calls,
                "reasoning": msg.get("reasoning_content") or ""}


KEY_FOR = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "grok": "XAI_API_KEY", "xai": "XAI_API_KEY",
           "kimi": "MOONSHOT_API_KEY", "moonshot": "MOONSHOT_API_KEY", "openrouter": "OPENROUTER_API_KEY",
           "ollama": None}
PERMANENT = ("HTTP 401", "HTTP 402", "HTTP 403", "HTTP 404", "insufficient", "quota", "balance", "invalid api key",
             "is missing")


class FallbackProvider:
    """Several providers in priority order. If the current one fails, Omni moves to the next and stays there
    when the failure looks permanent (bad key, no credit), so it doesn't keep retrying a dead provider."""

    def __init__(self, providers, on_switch=None):
        self.providers = providers
        self.i = 0
        self.on_switch = on_switch

    @property
    def model(self):
        return self.providers[self.i].model

    def chat(self, system, messages, tools):
        errors = []
        for j in range(self.i, len(self.providers)):
            p = self.providers[j]
            try:
                out = p.chat(system, messages, tools)
                if j != self.i:
                    old = self.providers[self.i].model
                    if any(k.lower() in errors[-1].lower() for k in PERMANENT):
                        self.i = j  # permanent failure: switch for the rest of the mission
                    if self.on_switch:
                        self.on_switch(old, p.model, errors[-1])
                return out
            except ProviderError as e:
                errors.append(f"{p.model}: {e}")
        raise ProviderError("All providers failed:\n" + "\n".join(errors))


def available_providers():
    """Provider names that have a key set (ollama is listed only if explicitly configured)."""
    return [n for n in ("anthropic", "openai", "grok", "kimi", "openrouter")
            if config.get(KEY_FOR[n])]


def make_chain(primary=None, model=None, on_switch=None):
    """Main provider + backups from OMNI_FALLBACK (e.g. 'kimi,grok,anthropic'). Providers without keys are skipped."""
    first = make_provider(primary, model)
    names = [n.strip().lower() for n in config.get("OMNI_FALLBACK", "").split(",") if n.strip()]
    chain, seen = [first], {(primary or config.get("OMNI_PROVIDER", "anthropic")).lower()}
    for n in names:
        if n in seen or (KEY_FOR.get(n) and not config.get(KEY_FOR[n])):
            continue
        try:
            chain.append(make_provider(n))
            seen.add(n)
        except ProviderError:
            pass
    return chain[0] if len(chain) == 1 else FallbackProvider(chain, on_switch)


def make_role_provider(role, default):
    """A separate provider for a role ('judge' or 'reflect'), e.g. OMNI_JUDGE_PROVIDER=grok. Falls back to default."""
    name = config.get(f"OMNI_{role.upper()}_PROVIDER")
    if not name:
        return default
    try:
        return make_provider(name, config.get(f"OMNI_{role.upper()}_MODEL") or None)
    except ProviderError:
        return default


def make_provider(name, model=None):
    name = (name or config.get("OMNI_PROVIDER", "anthropic")).lower()
    model = model or config.get("OMNI_MODEL") or config.DEFAULT_MODELS.get(name, "")
    if name == "anthropic":
        return AnthropicProvider(model)
    if name == "openai":
        return OpenAICompatProvider(model, config.get("OPENAI_BASE_URL", "https://api.openai.com/v1"), "OPENAI_API_KEY")
    if name == "openrouter":
        return OpenAICompatProvider(model, "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
    if name in ("grok", "xai"):
        return OpenAICompatProvider(model, config.get("XAI_BASE_URL", "https://api.x.ai/v1"), "XAI_API_KEY")
    if name in ("kimi", "moonshot"):
        return OpenAICompatProvider(model, config.get("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1"),
                                    "MOONSHOT_API_KEY")
    if name == "ollama":
        return OpenAICompatProvider(model, config.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"), None)
    raise ProviderError(f"Unknown provider '{name}'. Use anthropic, openai, grok, kimi, openrouter or ollama.")
