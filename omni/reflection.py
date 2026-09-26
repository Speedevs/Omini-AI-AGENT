"""
BACKGROUND REFLECTION: the agent's "inner voice".

While the agent works, a separate thread wakes up every few minutes (OMNI_REFLECT_EVERY, default 180 seconds)
and thinks about the mission on its own, in parallel, even while a long command, download or recording is
running. It reads the goal, the plan, the evidence so far and the latest steps, then asks:
  - Honestly, how much real progress has been made?
  - Is the agent stuck, looping, or drifting from the goal?
  - What is it not seeing?
  - What single course correction would help most?
Its conclusion is handed to the agent at its next step, shown on the dashboard, and sealed into the receipt.

A code-level stuck detector also fires a reflection immediately if the agent repeats the same action 3 times.
"""
import json
import queue
import threading
import time

REFLECT_PROMPT = """You are the background reflection process of an autonomous AI agent: its inner voice.
You are NOT doing the task. You run in parallel and think about how the task is going, then advise.
Be blunt and specific. Reply in at most 120 words with exactly these four lines:
PROGRESS: <honest assessment, including what is actually verified>
RISK: <stuck, looping, drifting, overconfident, or 'none'>
BLIND SPOT: <something the agent hasn't considered>
NEXT: <the single most valuable course correction>"""


class Reflector:
    def __init__(self, agent, every_seconds):
        self.agent = agent
        self.every = every_seconds
        self.inbox = queue.Queue()
        self.stop_flag = threading.Event()
        self.kick = threading.Event()
        self.reason = ""
        self.started = time.time()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.busy = threading.Lock()

    def start(self):
        if self.every > 0:
            self.thread.start()
        return self

    def stop(self):
        self.stop_flag.set()
        self.kick.set()

    def trigger(self, reason):
        """Ask for a reflection right now (e.g. the agent looks stuck)."""
        self.reason = reason
        self.kick.set()

    def drain(self):
        notes = []
        while not self.inbox.empty():
            notes.append(self.inbox.get_nowait())
        return notes

    def _snapshot(self):
        a = self.agent
        state = a.state.snapshot()
        recent = []
        for m in list(getattr(a, "_messages", []))[-14:]:
            if m["role"] == "assistant":
                calls = ", ".join(f"{c['name']}({json.dumps(c['input'])[:120]})" for c in m.get("tool_calls", []))
                recent.append(f"AGENT: {m.get('content', '')[:300]} {calls}")
            elif m["role"] == "tool":
                recent.append(f"RESULT of {m.get('name')}: {m['content'][:300]}")
        claims = "; ".join(f"#{i + 1} {c['status']}{'/' + c['judge'] if c.get('judge') else ''}: {c['claim'][:90]}"
                           for i, c in enumerate(state["claims"])) or "none yet"
        plan = "; ".join(f"[{p.get('status')}] {p.get('step')}" for p in state["plan"]) or "no plan yet"
        mins = int((time.time() - self.started) // 60)
        return (f"GOAL: {a.goal}\nTIME ELAPSED: {mins} min, step {state['step']}\nPLAN: {plan}\n"
                f"EVIDENCE: {claims}\n" + (f"TRIGGER: {self.reason}\n" if self.reason else "") +
                "LATEST STEPS:\n" + "\n".join(recent))

    def _reflect(self):
        with self.busy:
            try:
                snap = self._snapshot()
                text = self.agent.reflect_provider.chat(REFLECT_PROMPT, [{"role": "user", "content": snap}], [])["content"]
            except Exception as e:
                text = f"(reflection failed: {e})"
            mins = time.time() - self.started
            stamp = f"{int(mins // 60)}:{int(mins % 60):02d}"
            note = {"at": stamp, "text": text.strip(), "trigger": self.reason or "timer"}
            self.reason = ""
            if self.stop_flag.is_set():
                return  # mission already finished while this thought was in progress; drop it
            self.inbox.put(note)
            self.agent.on_reflection(note)

    def _run(self):
        while not self.stop_flag.is_set():
            fired = self.kick.wait(timeout=self.every)
            self.kick.clear()
            if self.stop_flag.is_set():
                break
            if fired or not self.stop_flag.is_set():
                self._reflect()
