"""Long-term memory: a JSON file of notes the agent can save and search across runs."""
import json
import re
import time
from pathlib import Path


class Memory:
    def __init__(self, path: Path):
        self.path = path
        self.notes = []
        if path.exists():
            try:
                self.notes = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self.notes = []

    def add(self, topic, note):
        self.notes.append({"topic": topic, "note": note, "time": time.strftime("%Y-%m-%d %H:%M")})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.notes, indent=2, ensure_ascii=False), encoding="utf-8")

    def search(self, query, k=8):
        words = set(re.findall(r"\w+", query.lower()))
        scored = []
        for n in self.notes:
            text = (n["topic"] + " " + n["note"]).lower()
            score = sum(text.count(w) for w in words)
            if score:
                scored.append((score, n))
        scored.sort(key=lambda x: -x[0])
        if not scored:
            return "Nothing relevant in memory."
        return "\n\n".join(f"[{n['time']}] {n['topic']}: {n['note']}" for _, n in scored[:k])

    def recent_topics(self, k=15):
        return ", ".join(n["topic"] for n in self.notes[-k:]) or "(none yet)"
