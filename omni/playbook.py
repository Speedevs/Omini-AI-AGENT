"""
EVOLVING PLAYBOOK - Omni gets better at *how it works* over time.

Memory (memory.py) stores facts about topics. The playbook stores lessons about method, like
"free search gets rate-limited, so fetch known sources directly" or "run a quick toy simulation
before a big one". After each mission, Omni holds a short retrospective and writes 1-3 lessons.
The most recent lessons are placed in the instructions of every future mission.
You can open workspace/.omni/playbook.md to read, edit or delete lessons.
"""
import json
import re
import time
from pathlib import Path

RETRO_PROMPT = """You just finished a mission. Look at the work log below and write 1 to 3 short, reusable lessons
about HOW to work better next time (method, tools, pitfalls), not facts about the topic.
Each lesson must be one sentence, specific and actionable.
Reply with ONLY a JSON list of strings, for example: ["lesson one", "lesson two"]

WORK LOG:
"""


class Playbook:
    def __init__(self, path: Path):
        self.path = path

    def lessons(self):
        if not self.path.exists():
            return []
        return [l[2:].strip() for l in self.path.read_text(encoding="utf-8").splitlines() if l.startswith("- ")]

    def for_prompt(self, k=10):
        ls = self.lessons()[-k:]
        return "\n".join(f"- {l}" for l in ls) if ls else "(no lessons yet: this is an early mission)"

    def retrospective(self, provider, log_text):
        try:
            reply = provider.chat("You are a concise reviewer of AI agent work.",
                                  [{"role": "user", "content": RETRO_PROMPT + log_text[-30000:]}], [])["content"]
            m = re.search(r"\[.*\]", reply, re.S)
            new = [str(x).strip() for x in json.loads(m.group(0))][:3] if m else []
        except Exception:
            return []
        existing = set(self.lessons())
        new = [l for l in new if l and l not in existing]
        if new:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                if self.path.stat().st_size == 0:
                    f.write("# Omni playbook: lessons learned across missions (edit freely)\n\n")
                for l in new:
                    f.write(f"- {l}\n")
                f.write(f"<!-- added {time.strftime('%Y-%m-%d %H:%M')} -->\n")
        return new
