"""
Offline demo of PROOF-CARRYING ANSWERS, no API key needed.

REAL: fetching Wikipedia, the code quote checks, source snapshots, corroboration searches, the grounding gate,
the hash-chained receipt, the proof page, and the independent verifier (including the tamper test).
SCRIPTED: the AI's decisions and the blind judge's verdicts (those need a real model).

    python examples/demo_proof.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from omni import dashboard, ui  # noqa: E402
from omni.agent import Agent  # noqa: E402
from omni.state import MissionState  # noqa: E402

DELAY = 0 if "--fast" in sys.argv else float(os.environ.get("DEMO_DELAY", "1.3"))
KRAS = "https://en.wikipedia.org/wiki/KRAS"
LUNG_QUOTE = ("In 2021, the U.S. FDA approved one KRAS G12C mutant covalent inhibitor, sotorasib (AMG 510, Amgen) "
              "for the treatment of non-small cell lung cancer")
SIM = ("import os\nos.makedirs('sim', exist_ok=True)\nout=[]\n"
       "for name, dose in [('max dose', 1.0), ('adaptive', 0.5)]:\n"
       "    s, r = 0.99, 0.01\n"
       "    for t in range(200):\n"
       "        tot = s + r; s += 0.05*s*(1-tot) - 0.08*dose*s; r += 0.03*r*(1-tot)\n"
       "    out.append(f'{name}: resistant fraction after 200 days = {r/(s+r):.2f}')\n"
       "open('sim/results.txt', 'w').write('\\n'.join(out)); print('\\n'.join(out))")

FIRST_DRAFT = ("KRAS G12D is the most common KRAS mutation, present in up to 37% of pancreatic cancers. "
               "Sotorasib, the first KRAS drug, is approved to treat pancreatic cancer. "
               "About 90% of patients die within five years of diagnosis. "
               "In a toy simulation, maximum-dose therapy left the tumor fully resistant (1.00), while adaptive "
               "dosing slowed resistance (0.73). "
               "This supports studying adaptive dosing, an idea borrowed from pesticide-resistance management.")
SECOND_DRAFT = ("KRAS G12D is the most common KRAS mutation, present in up to 37% of pancreatic cancers. "
                "No KRAS drug is established for pancreatic cancer in the sources I checked; sotorasib is approved "
                "for a lung cancer mutation instead. "
                "In a toy simulation, maximum-dose therapy left the tumor fully resistant (1.00), while adaptive "
                "dosing slowed resistance (0.73). "
                "An unverified figure I recall is that around 90% of patients die within five years. "
                "This supports studying adaptive dosing, an idea borrowed from pesticide-resistance management.")


def call(tool, **a):
    return {"id": f"{tool}_{time.time_ns()}", "name": tool, "input": a}


class ScriptedModel:
    model = "scripted demo model (no API key)"

    def chat(self, system, messages, tools):
        time.sleep(DELAY)
        if system.startswith("You are a blind fact judge"):  # scripted verdicts (a real model decides these)
            text = messages[0]["content"].lower()
            claim = text.split("passage from the source")[0]
            if "pancreatic" in claim and "sotorasib" in claim:  # scripted stand-in for a real judgment
                return {"content": '{"verdict": "contradicts", "reason": "The passage says sotorasib was approved '
                                   'for non-small cell lung cancer, not pancreatic cancer."}'}
            return {"content": '{"verdict": "supports", "reason": "The passage states this directly."}'}
        if system.startswith("You are a concise reviewer"):
            return {"content": '["Match claim wording to the source\'s exact scope (disease, mutation, number) '
                               'before recording it; the blind judge rejects overreach."]'}
        n = sum(1 for m in messages if m["role"] == "assistant")
        last = messages[-1].get("content", "") if messages[-1]["role"] == "tool" else ""
        steps = [
            ("", [call("update_plan", steps=[
                {"step": "Size the problem from a real source", "status": "doing"},
                {"step": "Test adaptive dosing in a toy model", "status": "todo"},
                {"step": "Pass the grounding gate and seal the proof", "status": "todo"}]),
                call("record_claim", claim="KRAS G12D is the most common KRAS mutation, in up to 37% of pancreatic cancers",
                     source=KRAS, quote="The most common KRAS mutation is G12D which is estimated to be present in up "
                                        "to 37% pancreatic cancers")]),
            ("Recording a claim about the first KRAS drug.",
             [call("record_claim", claim="Sotorasib, the first KRAS drug, is approved to treat pancreatic cancer",
                   source=KRAS, quote=LUNG_QUOTE)]),
            ("The quote is real, but the blind judge says it doesn't support my claim: the approval was for lung "
             "cancer. Retracting it.",
             [call("retract_claim", number=2, reason="Blind judge: the source says lung cancer, not pancreatic."),
              call("record_claim", claim="Sotorasib was approved in 2021 for non-small cell lung cancer",
                   source=KRAS, quote=LUNG_QUOTE)]),
            ("", [call("update_plan", steps=[
                {"step": "Size the problem from a real source", "status": "done"},
                {"step": "Test adaptive dosing in a toy model", "status": "doing"},
                {"step": "Pass the grounding gate and seal the proof", "status": "todo"}]),
                call("run_python", code=SIM)]),
            ("", [call("record_claim", claim="In the toy model, max-dose leaves resistant fraction 1.00 and adaptive 0.73",
                       source="workspace:sim/results.txt",
                       quote="max dose: resistant fraction after 200 days = 1.00\nadaptive: resistant fraction after 200 days = 0.73")]),
            ("Submitting my first draft.",
             [call("update_plan", steps=[
                 {"step": "Size the problem from a real source", "status": "done"},
                 {"step": "Test adaptive dosing in a toy model", "status": "done"},
                 {"step": "Pass the grounding gate and seal the proof", "status": "doing"}]),
              call("final_answer", answer=FIRST_DRAFT)]),
            ("The gate is right: I never verified the 90% figure, and the sotorasib sentence rests on a claim I "
             "retracted. Fixing both.",
             [call("update_plan", steps=[
                 {"step": "Size the problem from a real source", "status": "done"},
                 {"step": "Test adaptive dosing in a toy model", "status": "done"},
                 {"step": "Pass the grounding gate and seal the proof", "status": "done"}]),
              call("final_answer", answer=SECOND_DRAFT)]),
        ]
        content, calls = steps[min(n, len(steps) - 1)]
        return {"content": content, "tool_calls": calls}


if __name__ == "__main__":
    ws = Path(tempfile.mkdtemp(prefix="omni_proof_demo_"))
    state = MissionState()
    if "--no-dashboard" not in sys.argv:
        print(ui.cyan(f"Mission control: {dashboard.start(state, int(os.environ.get('DEMO_PORT', '8765')), '--no-browser' not in sys.argv, ws)}"))
        time.sleep(DELAY)
    goal = "What do we really know about KRAS G12D pancreatic cancer, and is adaptive dosing worth studying?"
    print(ui.cyan(ui.bold("OMNI - proof-carrying answers demo (real checks, scripted AI + judge)")))
    print(ui.yellow("goal> ") + goal + "\n")
    answer = Agent(ScriptedModel(), ws, auto_approve=True, max_steps=12, state=state).run(goal)
    print(ui.bold("\n=========== FINAL ANSWER ===========\n") + answer)

    receipt = next(ws.glob("missions/*/receipt.json"))
    root = state.snapshot()["root"]
    short = f"{receipt.parent.name}/receipt.json"
    print(ui.bold("\n=========== ANYONE CAN VERIFY IT (offline, no AI) ==========="))
    print(ui.dim(f"$ python -m omni.verify {short} --root {root[:12]}..."))
    time.sleep(DELAY)
    subprocess.run([sys.executable, "-m", "omni.verify", str(receipt), "--root", root], cwd=ROOT)

    print(ui.bold("\n=========== TAMPER TEST 1: change 37% to 47% in the answer ==========="))
    fake = receipt.parent.parent / "tampered"
    shutil.copytree(receipt.parent, fake)
    doc = json.loads((fake / "receipt.json").read_text(encoding="utf-8"))
    doc["answer"] = doc["answer"].replace("37%", "47%")
    (fake / "receipt.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    print(ui.dim(f"$ python -m omni.verify tampered/receipt.json --root {root[:12]}..."))
    time.sleep(DELAY)
    subprocess.run([sys.executable, "-m", "omni.verify", str(fake / "receipt.json"), "--root", root], cwd=ROOT)

    print(ui.bold("\n=========== TAMPER TEST 2: a smarter forger also rebuilds every hash ==========="))
    from omni.receipts import record_hash, sha
    prev = "0" * 64
    for rec in doc["chain"]:
        if rec["kind"] == "final_answer":
            rec["data"]["answer_sha256"] = sha(doc["answer"])
        from omni.evidence import ground
        rec["prev"] = prev
        rec["hash"] = prev = record_hash(rec)
    doc["root"] = prev
    doc["grounding"] = ground(doc["answer"], doc["claims"])
    (fake / "receipt.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    print(ui.dim(f"$ python -m omni.verify tampered/receipt.json --root {root[:12]}...   (the root you kept)"))
    time.sleep(DELAY)
    subprocess.run([sys.executable, "-m", "omni.verify", str(fake / "receipt.json"), "--root", root], cwd=ROOT)
    time.sleep(float(os.environ.get("DEMO_HOLD", "0")))
