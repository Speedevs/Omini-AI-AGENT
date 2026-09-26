"""
The brain loop. In plain words:

  1. Give the model the goal, a set of tools, and instructions on how to work.
  2. The model replies with thoughts and/or tool calls.
  3. We run the tools and send back the results.
  4. Repeat until it calls final_answer or runs out of steps.

Extra tricks on top of the basic loop:
  - A system prompt that pushes for planning, multiple hypotheses, self-critique and verification.
  - Periodic "checkpoints" that force the agent to step back and reflect.
  - Sub-agents (delegate) for independent chunks of work, each with a clean context.
  - Context compaction: when the history gets huge, older steps are summarized.
  - Long-term memory that persists between runs.

What makes Omni different (see README "What's unique"):
  - EVIDENCE LEDGER: claims are checked against their sources by code, not by the AI (evidence.py).
  - COUNCIL: three opposed thinkers run in parallel at key decisions (convene_council).
  - EVOLVING PLAYBOOK: lessons about method carry over to every future mission (playbook.py).
  - MISSION CONTROL: a live dashboard in your browser (dashboard.py).
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
import re

from .evidence import JUDGE_PROMPT, audit_text, ground, numbers_in, observed_from_chain
from .receipts import Receipt, sha
from .reflection import Reflector
from .memory import Memory
from .playbook import Playbook
from .state import NULL_STATE
from .tools import Toolbox, system_info
from .ui import yellow, bold, cyan, dim, green, red

SYSTEM_PROMPT = """You are Omni, an autonomous problem-solving agent running on the user's computer ({sysinfo}).
Workspace folder (your sandbox): {workspace}
Today's date: {date}
Topics already in long-term memory: {memory}

YOUR PLAYBOOK (lessons you learned on earlier missions; follow them):
{playbook}

HOW YOU WORK
1. Understand the real goal. If it is vague, pick the most useful concrete interpretation and say which you chose.
2. Plan with update_plan. Break big goals into steps you can actually finish with your tools.
3. Think beyond the obvious. Before committing, use `think` to list at least 3 different approaches,
   including one unconventional one (analogy from another field, reversing the problem, first principles,
   removing an assumption everyone makes). Pick based on expected value, not habit.
4. Act. Use tools rather than guessing: search and read real sources, write and run code to test ideas,
   compute numbers instead of estimating them.
5. Verify. Check results with an independent method where possible. Try to break your own conclusions.
   Your final answer is checked by a GROUNDING GATE: every sentence containing a number must be backed by a
   verified claim in the ledger, have appeared in output your own tools returned this mission (command
   results, files you read), or be clearly labeled as an estimate, or it gets sent back. record_claim is for
   facts taken from sources; results of your own commands are already tracked automatically.
   Each claim is also shown, without your reasoning, to a blind judge, and checked against other websites.
   Register every key fact your answer depends on with record_claim (exact quote + source). Omni checks it
   against the source itself. If a claim comes back NOT FOUND, re-check it or retract_claim it; never repeat it as fact.
   At big decision points, use convene_council to get opposing views before committing.
6. Delegate independent sub-tasks to sub-agents when it keeps your own context focused.
7. Save durable lessons and results with `remember` so future runs build on this one.
8. Finish with final_answer: what you did, what you found, files you created, what is verified vs. speculative,
   and sensible next steps.

HONESTY RULES
- Never fabricate sources, data, citations or results. If a tool fails, say so.
- Distinguish clearly between established facts, your inferences, and speculation.
- For grand goals (curing a disease, proving a famous conjecture), do genuine, scoped, useful work
  (literature synthesis, hypothesis generation, data analysis, experiment designs) and be explicit that
  real-world validation by experts, labs and trials is required. Do not claim breakthroughs you cannot verify.
- Do not give medical, legal or financial instructions as if you were a licensed professional.

SCOPE
- Change only what the user asked for. If an extra setting or step seems useful, suggest it in your answer
  instead of doing it.
- Never accept terms of service, privacy policies or other legal agreements on the user's behalf.

COMPUTER CONTROL
- Use launch_app to open apps (never run_shell for that; it would wait until the app closes).
- Use install_app / uninstall_app for software. For apps outside its catalog, verify the publisher first.
- Browser extensions: install_app opens the official store page (pass browser=...); the user clicks Add.
  Check the result: some extensions don't exist for some browsers. Removal: open_extensions_page.
- To configure Firefox use setup_firefox; launch_app('firefox') to open it afterwards.
- Games: install_app('subway surfers') opens the official web version (poki.com) for the user to play; you
  can't play fast reaction games. The user can play chess against you on the dashboard board at any time.
- Use browser for websites that need clicking or typing; use fetch_url for plain reading (faster).
- When you capture or create a file the user asked for, call send_file so they receive it.

SAFETY
- Crypto wallets: install ONLY via install_app (official links). Never search for wallet downloads, never create or
  import wallets, never ask for, read, store or type recovery phrases or private keys, never approve transactions.
- If a website asks for a password, payment or identity documents, stop and let the user do it themselves.
- Stay inside the workspace unless the user clearly asks otherwise.
- Never run destructive commands (deleting outside the workspace, formatting disks, disabling security)
  and never attempt to access accounts, systems or data you are not authorized to use.
"""

COUNCIL = {
    "Skeptic": "You are the Skeptic on a research council. Find the weakest assumption, the most likely way this "
               "is wrong, and what evidence would falsify it. Be concrete and brief (max 150 words).",
    "Inventor": "You are the Inventor on a research council. Propose 2-3 unconventional angles: analogies from other "
                "fields, inverting the problem, dropping an assumption. Be concrete and brief (max 150 words).",
    "Pragmatist": "You are the Pragmatist on a research council. Name the single cheapest, fastest next step that "
                  "would most reduce uncertainty, doable with web search and Python. Brief (max 120 words).",
}

CHECKPOINT = ("\n\n[CHECKPOINT] Step back: Is the current approach working? What is the strongest objection "
              "to your findings so far? Is there a better, less obvious path? Update the plan if needed.")


class Agent:
    def __init__(self, provider, workspace: Path, auto_approve=False, max_steps=40, depth=0, verbose=True,
                 state=None, receipt=None):
        self.provider = provider
        self.state = state or NULL_STATE
        self.ws = workspace
        self.ws.mkdir(parents=True, exist_ok=True)
        self.memory = Memory(self.ws / ".omni" / "memory.json")
        self.playbook = Playbook(self.ws / ".omni" / "playbook.md")
        self.max_steps = max_steps
        self.depth = depth
        self.verbose = verbose
        self.auto = auto_approve
        self.receipt = receipt or Receipt(self.ws / ".omni" / "snapshots")
        from .providers import make_role_provider
        self.judge_provider = make_role_provider("judge", provider)      # e.g. a different company = more independent
        self.reflect_provider = make_role_provider("reflect", provider)
        if hasattr(provider, "on_switch"):
            provider.on_switch = self._provider_switched
        self.tools = Toolbox(self.ws, self.memory, auto_approve, depth, spawn=self._spawn_subagent,
                             state=self.state, council=self._council, judge=self._judge, receipt=self.receipt)
        self.context_limit = int(config.get("OMNI_CONTEXT_CHARS", "300000"))
        self.checkpoint_every = int(config.get("OMNI_CHECKPOINT_EVERY", "8"))

    def _log(self, text, kind=None, plain=None):
        if self.verbose:
            print(("    " * self.depth) + text)
        if kind:
            self.state.event(kind, plain if plain is not None else text, self.depth)

    def _system(self):
        return SYSTEM_PROMPT.format(sysinfo=system_info(), workspace=self.ws, date=time.strftime("%Y-%m-%d"),
                                    memory=self.memory.recent_topics(), playbook=self.playbook.for_prompt())

    def _spawn_subagent(self, task):
        self._log(cyan(f"  -> sub-agent started: {task[:120]}"), "agent", f"Sub-agent started: {task}")
        idx = self.state.add("agents", {"task": task, "depth": self.depth + 1, "status": "working"})
        sub = Agent(self.provider, self.ws, self.auto, max_steps=max(10, self.max_steps // 2),
                    depth=self.depth + 1, verbose=self.verbose, state=self.state, receipt=self.receipt)
        result = sub.run(task)
        self.state.update_item("agents", idx, status="done", result=result[:300])
        self._log(cyan("  <- sub-agent finished"), "agent", "Sub-agent finished")
        return result

    def _provider_switched(self, old, new, why):
        self._log(red(f"  provider switch: {old} failed, continuing with {new}  ({why[:120]})"), "gate",
                  f"Provider switch: {old} failed; continuing with {new}")
        self.receipt.add("provider_switch", {"from": old, "to": new, "reason": why[:300]})

    def on_reflection(self, note):
        """Called from the background thread when the inner voice has something to say."""
        self._log(cyan(f"  [reflection @ {note['at']}, {note['trigger']}] ") + note["text"].replace("\n", " | "),
                  "reflect", f"@{note['at']} ({note['trigger']}): {note['text']}")
        self.receipt.add("reflection", note)

    def _judge(self, claim, passage):
        """BLIND JUDGE: a fresh model call that sees only the claim and the passage code cut from the source."""
        try:
            reply = self.judge_provider.chat(JUDGE_PROMPT, [{"role": "user", "content":
                                       f"CLAIM:\n{claim}\n\nPASSAGE FROM THE SOURCE:\n{passage}"}], [])["content"]
            v = json.loads(re.search(r"\{.*\}", reply, re.S).group(0))
            if v.get("verdict") in ("supports", "partial", "contradicts", "unrelated"):
                return v
        except Exception:
            pass
        return {"verdict": None, "reason": "judge unavailable"}

    def _gate(self, answer, final=False):
        """GROUNDING GATE: code checks every sentence of the answer against the evidence ledger."""
        obs = observed_from_chain(self.receipt.chain)
        g = ground(answer, self.state.snapshot()["claims"], obs)
        bad = [x["text"] + (f"   (repeats rejected claim #{x['claim']})" if x["kind"] == "echo" else "")
               for x in g if x["kind"] in ("unbacked", "echo")]
        if final:  # second attempt: accept, but visibly mark whatever is still unbacked
            for x in g:
                if x["kind"] in ("unbacked", "echo"):
                    t = x["text"]
                    marked = (t[:-1] + " [unbacked]" + t[-1]) if t[-1:] in ".!?" else t + " [unbacked]"
                    answer = answer.replace(t, marked, 1)  # label goes before the final period
            return answer, ground(answer, self.state.snapshot()["claims"], obs), bad
        return answer, g, bad

    def _council(self, question, context=""):
        """Three opposed perspectives answer at the same time (parallel threads), then the agent synthesizes."""
        self._log(cyan(f"  council convened: {question[:120]}"), "council", f"Council convened: {question}")
        prompt = f"Mission goal: {self.goal}\n\nContext so far: {context or '(none)'}\n\nQuestion: {question}"

        def ask(name):
            try:
                return name, self.provider.chat(COUNCIL[name], [{"role": "user", "content": prompt}], [])["content"]
            except Exception as e:
                return name, f"(unavailable: {e})"

        with ThreadPoolExecutor(max_workers=3) as pool:
            views = dict(pool.map(ask, COUNCIL))
        self.state.add("council", {"question": question, "views": views})
        for name, v in views.items():
            self._log(dim(f"      {name}: {v[:200]}"))
        return "\n\n".join(f"{name.upper()}:\n{v}" for name, v in views.items()) + \
            "\n\nNow weigh these views yourself and decide."

    def _size(self, messages):
        return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)

    def _compact(self, messages):
        """Summarize the middle of a long history so the agent can keep working for a long time."""
        if self._size(messages) < self.context_limit or len(messages) < 10:
            return messages
        # Keep the tail starting at an assistant message so tool calls and results stay paired.
        cut = len(messages) - 6
        while cut > 1 and messages[cut]["role"] != "assistant":
            cut -= 1
        middle = messages[1:cut]
        if not middle:
            return messages
        self._log(dim("  (compacting older history into a summary...)"))
        transcript = json.dumps(middle, ensure_ascii=False)[: self.context_limit // 2]
        summary = self.provider.chat(
            "You compress agent work logs. Keep every concrete finding, number, source URL, file created, "
            "failed approach and open question. Drop chatter.",
            [{"role": "user", "content": "Summarize this work log:\n" + transcript}], [])["content"]
        head = {"role": "user", "content": messages[0]["content"] + "\n\n[SUMMARY OF YOUR EARLIER WORK]\n" + summary}
        return [head] + messages[cut:]

    def run(self, goal):
        self.goal = goal
        if self.depth == 0:
            self.state.set(goal=goal, model=self.provider.model, status="working", step=0, answer="",
                           started=time.time(), lessons=self.playbook.lessons()[-10:])
        self._reflector = None
        if self.depth == 0:
            self.receipt.add("mission", {"goal": goal, "model": self.provider.model})
            self._reflector = Reflector(self, int(config.get("OMNI_REFLECT_EVERY", "180"))).start()
        try:
            answer = self._loop(goal)
        finally:
            if self._reflector:
                self._reflector.stop()
        if self.depth > 0:
            return answer
        claims = self.state.snapshot()["claims"]
        grounding = self._last_grounding or ground(answer, claims, observed_from_chain(self.receipt.chain))
        folder = self.ws / "missions" / time.strftime("%Y%m%d_%H%M%S")
        rpath, ppath = self.receipt.export(folder, goal, self.provider.model, answer, claims, grounding)
        root = self.receipt.chain[-1]["hash"]
        from .desktop import send_telegram_text
        anchored = send_telegram_text(f"Omni proof root for: {goal[:200]}\n{root}\n"
                                      f"Verify: python -m omni.verify receipt.json --root {root}")
        self.state.set(root=root)
        for pth, label in ((ppath, "Proof page"), (rpath, "Tamper-evident receipt")):
            self.state.add("files", {"path": pth.relative_to(self.ws).as_posix(), "label": label,
                                     "time": time.strftime("%H:%M:%S")})
        counts = {k: sum(1 for x in grounding if x["kind"] in ((k, "echo") if k == "unbacked" else (k,)))
                  for k in ("backed", "observed", "speculative", "unbacked")}
        summary = (f"{counts['backed']} backed by sources, {counts['observed']} seen in tool output, "
                   f"{counts['speculative']} speculative, {counts['unbacked']} unbacked")
        self._log(green(f"  proof sealed: {summary} -> {ppath}"), "proof", f"Proof sealed: {summary}")
        changes = [e for e in self.tools.journal._load() if e["mission"] == self.tools.journal.mission]
        if not claims and not counts["observed"] and changes:
            answer += ("\n\n--- Evidence audit ---\nNo factual claims to check: this was an action mission. "
                       "Every change it made is listed in the undo journal.")
        else:
            answer += audit_text(claims, counts["observed"])
        if changes:
            answer += (f"\n\nChanges to your computer this mission: {len(changes)}. Reverse them any time with: "
                       f"python -m omni.undo --workspace \"{self.ws}\"")
        answer += (f"\n\nProof page: {ppath}\nReceipt: {rpath}\nRoot hash: {root}"
                   f"{' (copy sent to your Telegram)' if anchored else ' (keep a copy somewhere else)'}\n"
                   f"Anyone can re-check it offline: python -m omni.verify \"{rpath}\" --root {root}")
        self._log(dim("  (holding a short retrospective to improve the playbook...)"), "retro", "Retrospective started")
        new = self.playbook.retrospective(self.provider, json.dumps(self._messages, ensure_ascii=False))
        for l in new:
            self._log(green(f"  + new playbook lesson: {l}"), "lesson", f"New lesson: {l}")
        self.state.set(status="done", answer=answer, lessons=self.playbook.lessons()[-10:])
        return answer

    def _deliver_reflections(self, messages):
        if not self._reflector:
            return
        notes = self._reflector.drain()
        if not notes:
            return
        if messages[-1]["role"] != "tool":  # nothing to attach to yet; keep them for the next step
            for n in notes:
                self._reflector.inbox.put(n)
            return
        messages[-1]["content"] += "".join(
            f"\n\n[BACKGROUND REFLECTION @ {n['at']}, trigger: {n['trigger']}]\n{n['text']}\n"
            f"(Your own inner voice, thinking in parallel. Weigh it; act on it if it's right.)" for n in notes)

    def _watch_for_loops(self, name, args):
        """Stuck detector (code): the same action 3 times in a row triggers an immediate reflection."""
        key = name + json.dumps(args, sort_keys=True)[:500]
        self._recent = (getattr(self, "_recent", []) + [key])[-3:]
        if self._reflector and len(self._recent) == 3 and len(set(self._recent)) == 1 and name not in ("think",):
            self._log(red(f"  stuck detector: '{name}' repeated 3 times, asking for a reflection now"),
                      "gate", f"Stuck detector: '{name}' repeated 3 times; reflecting now")
            self._reflector.trigger(f"The agent called {name} with identical arguments 3 times in a row.")
            self._recent = []

    def _loop(self, goal):
        self._last_grounding, gate_used = None, False
        messages = [{"role": "user", "content": goal}]
        self._messages = messages
        log_dir = self.ws / ".omni" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}_d{self.depth}.json"

        for step in range(1, self.max_steps + 1):
            messages = self._compact(messages)
            self._messages = messages
            if self.depth == 0:
                self.state.set(step=step)
                self._deliver_reflections(messages)
            reply = self.provider.chat(self._system(), messages, self.tools.specs())
            messages.append({"role": "assistant", "content": reply["content"], "tool_calls": reply["tool_calls"],
                             **({"reasoning": reply["reasoning"]} if reply.get("reasoning") else {})})
            log_file.write_text(json.dumps(messages, indent=1, ensure_ascii=False), encoding="utf-8")

            if reply["content"].strip():
                self._log(dim(f"[{step}] ") + reply["content"].strip(), "say", reply["content"].strip())
            self.receipt.add("ai", {"depth": self.depth, "text": reply["content"],
                                    "calls": [{"name": c["name"], "input": c["input"]} for c in reply["tool_calls"]]})

            if not reply["tool_calls"]:
                # Some models occasionally write tool calls as TEXT instead of making them. Don't accept that
                # as a final answer; send it back (twice at most).
                if re.search(r"<invoke|<function_calls|</parameter>|<tool_call>", reply["content"]) and \
                        getattr(self, "_fake_calls", 0) < 2:
                    self._fake_calls = getattr(self, "_fake_calls", 0) + 1
                    self._log(red(f"[{step}] reply contained tool-call text instead of a real tool call; sent back"),
                              "gate", "Reply contained tool-call text instead of a real tool call; sent back")
                    messages.append({"role": "user", "content": "Your last message contained tool-call markup as "
                                     "plain text, so nothing ran. Make real tool calls, and finish with the "
                                     "final_answer tool."})
                    continue
                return reply["content"]  # model answered directly

            final = None
            for tc in reply["tool_calls"]:
                name, args = tc["name"], tc["input"]
                if final is not None:  # anything after final_answer in the same reply is skipped
                    messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": "Skipped."})
                    continue
                if name == "final_answer":
                    answer = args.get("answer", "")
                    if self.depth > 0:
                        return answer
                    answer, g, bad = self._gate(answer, final=gate_used)
                    if bad and not gate_used:
                        gate_used = True
                        msg = ("GROUNDING GATE (checked by code): these sentences state facts that no verified claim in "
                               "the evidence ledger backs, that didn't appear in any output you actually got from "
                               "your tools, or that repeat a rejected claim:\n" + "\n".join(f"- {b}" for b in bad) +
                               "\nFor each one: back it with record_claim, clearly label it as an estimate or "
                               "speculation, or remove it. Then call final_answer again. Anything still unbacked "
                               "will be marked [unbacked] in the final answer.")
                        self._log(red(f"[{step}] grounding gate sent the answer back: {len(bad)} unbacked sentence(s)"),
                                  "gate", f"Grounding gate sent the answer back: {len(bad)} unbacked sentence(s)")
                        for b in bad:
                            self._log(dim(f"      - {b}"), "gate", f"Unbacked: {b}")
                        messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": msg})
                        self.receipt.add("gate", {"unbacked": bad})
                        final = False
                        continue
                    self._last_grounding = g
                    if bad:
                        self._log(red(f"  {len(bad)} sentence(s) still unbacked; marked [unbacked]"), "gate",
                                  f"{len(bad)} sentence(s) still unbacked; marked in the answer")
                    return answer
                preview = (f"{args.get('action')} {args.get('url') or args.get('element') or args.get('seconds') or ''}"
                           if name in ("browser", "screen") else "") or args.get("target") or args.get("name") \
                    or args.get("thought") or args.get("command") or args.get("query") or args.get("url") \
                    or args.get("path") or args.get("task") or ""
                self._log(green(f"[{step}] {name}") + dim(f" {str(preview)[:160]}"), name, str(preview))
                if self.depth == 0:
                    self._watch_for_loops(name, args)
                result = self.tools.run(name, args)
                if name not in ("think", "update_plan", "record_claim", "retract_claim", "convene_council"):
                    self._log(dim("      " + result[:300].replace("\n", " | ")), "result", result[:400])
                elif name in ("record_claim", "retract_claim"):
                    self._log(dim("      " + result[:200]))
                messages.append({"role": "tool", "tool_call_id": tc["id"], "name": name, "content": result})
                self.receipt.add("tool", {"depth": self.depth, "tool": name, "result_sha256": sha(result),
                                          "result_preview": result[:300],
                                          "observed_numbers": sorted(numbers_in(result))[:400]})

            left = self.max_steps - step
            if self.depth == 0 and left == 3 and self.max_steps >= 8 and messages[-1]["role"] == "tool":
                messages[-1]["content"] += (
                    "\n\n[BUDGET] Only 3 steps left. Stop researching. Now: do any quick actions the user asked for "
                    "that you haven't done yet (like opening an app or a page), then call final_answer with what you "
                    "have, saying clearly what you couldn't verify.")
                self._log(yellow("  budget: 3 steps left, told the agent to wrap up"), "gate", "3 steps left: wrap up")

            if step % self.checkpoint_every == 0:
                messages[-1]["content"] += CHECKPOINT  # tuck the reflection nudge into the last tool result

        # Out of steps: ask for a best-effort wrap-up from a TRIMMED history (fast), with a code fallback.
        claims = self.state.snapshot()["claims"]
        facts = "\n".join(f"- [{c['status']}] {c['claim']}" for c in claims) or "- none"
        recent = []
        for m in messages[-8:]:
            if m["role"] == "assistant":
                recent.append("YOU: " + (m.get("content") or "")[:400] + " " +
                              ", ".join(c["name"] for c in m.get("tool_calls", [])))
            elif m["role"] == "tool":
                recent.append(f"RESULT {m.get('name')}: {m['content'][:500]}")
        brief = (f"GOAL: {goal}\nVERIFIED/CHECKED CLAIMS:\n{facts}\nLAST STEPS:\n" + "\n".join(recent) +
                 "\n\nYou are out of steps. Write the final answer now: what you accomplished, what you could not "
                 "finish or verify, and next steps. Only state facts listed as verified above.")
        try:
            return self.provider.chat("You are Omni, writing the final answer of a mission.",
                                       [{"role": "user", "content": brief}], [])["content"]
        except Exception as e:
            return (f"I ran out of steps before finishing, and couldn't reach the AI to write a summary ({e}).\n"
                    f"Checked claims so far:\n{facts}")
