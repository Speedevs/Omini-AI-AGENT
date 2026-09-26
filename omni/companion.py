"""
COMPANION MODE: Omni starts conversations instead of only waiting for orders.

    python -m omni --companion            (or set OMNI_COMPANION_MINUTES=10 in .env)

When you open it, Omni greets you with something relevant: a follow-up on your last mission, a useful next
step, or a question. If you go quiet for N minutes, it checks in (and messages your Telegram if set up).
It stops after 3 check-ins you don't answer, so it never nags.
"""
import json
import threading
import time
from pathlib import Path

OPENER_PROMPT = """You are Omni, a helpful AI agent that lives on the user's computer, in companion mode.
Start a conversation with the user. Write ONE short, warm message (at most 2 sentences) that is genuinely
useful: follow up on their last mission, suggest a sensible next step, or ask how something went.
Use the context; don't invent facts about them. No more than one emoji. End with a question."""


def _context(ws: Path):
    parts = [f"Local time: {time.strftime('%A %H:%M')}"]
    try:
        receipts = sorted((ws / "missions").glob("*/receipt.json"))
        if receipts:
            doc = json.loads(receipts[-1].read_text(encoding="utf-8"))
            parts.append(f"Last mission goal: {doc['goal']}\nLast answer (start): {doc['answer'][:400]}")
    except Exception:
        pass
    try:
        notes = json.loads((ws / ".omni" / "memory.json").read_text(encoding="utf-8"))[-5:]
        parts.append("Things remembered: " + "; ".join(f"{n['topic']}: {n['note'][:80]}" for n in notes))
    except Exception:
        pass
    return "\n".join(parts) if len(parts) > 1 else parts[0] + "\n(This is a new user with no missions yet.)"


def opener(provider, ws: Path):
    try:
        return provider.chat(OPENER_PROMPT, [{"role": "user", "content": _context(ws)}], [])["content"].strip()
    except Exception:
        return "Hi! What would you like me to work on today?"


class Companion:
    def __init__(self, provider, ws, state, minutes, say):
        self.provider, self.ws, self.state, self.say = provider, ws, state, say
        self.every = minutes * 60
        self.last_activity = time.time()
        self.unanswered = 0
        self.busy = False
        self.stop = threading.Event()

    def user_spoke(self):
        self.last_activity = time.time()
        self.unanswered = 0

    def greet(self):
        self._speak(prompt=False)  # the input prompt comes right after the greeting anyway

    def _speak(self, prompt=True):
        text = opener(self.provider, self.ws)
        self.state.event("companion", text)
        self.say(text, prompt)
        from .desktop import send_telegram_text
        send_telegram_text(f"Omni: {text}")

    def start(self):
        def run():
            while not self.stop.wait(15):
                if self.busy or self.unanswered >= 3:
                    continue
                if time.time() - self.last_activity >= self.every:
                    self.unanswered += 1
                    self.last_activity = time.time()
                    self._speak()
        threading.Thread(target=run, daemon=True).start()
        return self
