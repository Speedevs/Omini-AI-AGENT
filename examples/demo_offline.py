"""
Offline demo of Omni's unique features, no API key needed.

The agent loop, tools, web search, Evidence Ledger checks, council threads, playbook and dashboard are all
REAL. Only the AI's decisions are scripted (by ScriptedModel below), so you can see how a mission flows.
One claim is deliberately fabricated to show the Evidence Ledger catching it.

Run from the project folder:
    python examples/demo_offline.py            (terminal + dashboard in your browser)
    python examples/demo_offline.py --fast     (no pauses)
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["OMNI_CHECKPOINT_EVERY"] = "4"

from omni import dashboard  # noqa: E402
from omni.agent import Agent  # noqa: E402
from omni.state import MissionState  # noqa: E402
from omni.ui import bold, cyan, yellow  # noqa: E402

DELAY = 0 if "--fast" in sys.argv else float(os.environ.get("DEMO_DELAY", "1.6"))
KRAS = "https://en.wikipedia.org/wiki/KRAS"
SIM = ("import os\nos.makedirs('sim', exist_ok=True)\nout=[]\n"
       "for name, dose in [('max dose', 1.0), ('adaptive', 0.5)]:\n"
       "    s, r = 0.99, 0.01\n"
       "    for t in range(200):\n"
       "        tot = s + r; s += 0.05*s*(1-tot) - 0.08*dose*s; r += 0.03*r*(1-tot)\n"
       "    out.append(f'{name}: resistant fraction after 200 days = {r/(s+r):.2f}')\n"
       "open('sim/results.txt', 'w').write('\\n'.join(out)); print('\\n'.join(out))")


def call(name, **args):
    return {"id": f"{name}_{time.time_ns()}", "name": name, "input": args}


class ScriptedModel:
    model = "scripted demo model (no API key)"

    def chat(self, system, messages, tools):
        time.sleep(DELAY)
        if "research council" in system:                      # the three council members
            if "Skeptic" in system:
                return {"content": "Toy models with invented rates prove nothing about patients. The key assumption "
                        "is that resistant cells grow slower than sensitive ones; if that is false, adaptive dosing fails."}
            if "Inventor" in system:
                return {"content": "Borrow from farming: pest managers keep pesticide-sensitive insects alive so they "
                        "outcompete resistant ones. Also invert it: instead of killing tumors, try to keep them stable."}
            return {"content": "Cheapest step: simulate both dosing strategies with one simple model, then check "
                    "real sources for how common KRAS G12D is, so the problem is sized correctly."}
        if system.startswith("You are a blind fact judge"):   # scripted judge verdict
            return {"content": '{"verdict": "supports", "reason": "The passage states this."}'}
        if system.startswith("You are a concise reviewer"):   # end-of-mission retrospective
            return {"content": '["Free web search can be rate-limited, so fetch known reference pages directly '
                    'when possible.", "Register numbers with record_claim before using them; one fabricated '
                    'statistic was caught this mission."]'}

        n = sum(1 for m in messages if m["role"] == "assistant")
        last = messages[-1].get("content", "")
        note = ("Checkpoint: my simulation uses made-up rates, so it is only a toy. I will label it speculative. "
                if "[CHECKPOINT]" in last else "")

        if messages[0]["content"].startswith("Size the problem"):  # the sub-agent's own mission
            steps = [
                [call("web_search", query="KRAS G12D pancreatic cancer frequency")],
                [call("record_claim", claim="KRAS G12D is the most common KRAS mutation, in up to 37% of pancreatic cancers",
                      source=KRAS, quote="The most common KRAS mutation is G12D which is estimated to be present "
                                         "in up to 37% pancreatic cancers")],
                [call("record_claim", claim="Sotorasib (2021) was the first KRAS inhibitor approved for clinical use",
                      source=KRAS, quote="In 2021, the U.S. FDA approved one KRAS G12C covalent inhibitor, sotorasib, "
                                         "for the treatment of non-small cell lung cancer, the first KRAS inhibitor "
                                         "to reach the market")],
                [call("final_answer", answer="G12D is the most common KRAS mutation (up to 37% of pancreatic cancers); "
                      "the first KRAS drug, sotorasib, targets a different mutation (G12C).")],
            ]
            return {"content": "", "tool_calls": steps[min(n, len(steps) - 1)]}

        steps = [
            ("", [call("think", thought="Three approaches: (1) hit the tumor with the maximum dose, (2) search for a new "
                       "drug target, (3) unconventional: treat resistance as evolution and manage it, like farmers "
                       "manage pesticide resistance."),
                  call("update_plan", steps=[
                      {"step": "Ask the council which angle to pursue", "status": "doing"},
                      {"step": "Size the problem with real sources", "status": "todo"},
                      {"step": "Simulate max-dose vs adaptive dosing", "status": "todo"},
                      {"step": "Audit claims and report honestly", "status": "todo"}])]),
            ("", [call("convene_council", question="Is managing drug resistance as an evolutionary process a "
                       "promising angle worth testing here?", context="Goal is exploratory research only.")]),
            ("The council agrees on a cheap test plus real-world sizing. Delegating the sizing to a helper.",
             [call("update_plan", steps=[
                 {"step": "Ask the council which angle to pursue", "status": "done"},
                 {"step": "Size the problem with real sources", "status": "doing"},
                 {"step": "Simulate max-dose vs adaptive dosing", "status": "todo"},
                 {"step": "Audit claims and report honestly", "status": "todo"}]),
              call("delegate", task="Size the problem: how common is KRAS G12D in pancreatic cancer, and what KRAS "
                                    "drugs exist? Register key facts with record_claim.")]),
            ("", [call("run_python", code=SIM)]),
            ("Recording the simulation result so it can be checked against the output file.",
             [call("record_claim", claim="In the toy model, max-dose therapy leaves the tumor fully resistant after 200 days",
                   source="workspace:sim/results.txt", quote="max dose: resistant fraction after 200 days = 1.00")]),
            ("Adding a statistic from memory (this is the kind of claim models get wrong).",
             [call("record_claim", claim="KRAS G12D appears in over 90% of pancreatic cancers",
                   source=KRAS, quote="G12D is found in over 90% of all pancreatic cancers")]),
            ("The ledger rejected the 90% figure: the source says up to 37%. Retracting it.",
             [call("retract_claim", number=4, reason="Source says up to 37%, not over 90%. My figure was wrong."),
              call("update_plan", steps=[
                 {"step": "Ask the council which angle to pursue", "status": "done"},
                 {"step": "Size the problem with real sources", "status": "done"},
                 {"step": "Simulate max-dose vs adaptive dosing", "status": "done"},
                 {"step": "Audit claims and report honestly", "status": "done"}]),
              call("remember", topic="adaptive therapy", note="Toy model: max dose selected full resistance; adaptive slowed it.")]),
            ("", [call("final_answer", answer=
                "KRAS G12D is the most common KRAS mutation, found in up to 37% of pancreatic cancers (verified).\n"
                "In a toy simulation, maximum-dose therapy let drug-resistant cells take over completely, while a "
                "gentler adaptive schedule slowed that (resistant fraction 0.73 vs 1.00). This supports exploring "
                "adaptive dosing, an idea borrowed from pesticide-resistance management.\n"
                "SPECULATIVE: the model's rates are invented. This is a hypothesis for experts, not medical evidence.")]),
        ]
        content, calls = steps[min(n, len(steps) - 1)]
        return {"content": (note + content).strip(), "tool_calls": calls}


if __name__ == "__main__":
    ws = Path(tempfile.mkdtemp(prefix="omni_demo_"))
    state = MissionState()
    if "--no-dashboard" not in sys.argv:
        print(cyan(f"Mission control: {dashboard.start(state, int(os.environ.get('DEMO_PORT', '8765')), '--no-browser' not in sys.argv)}"))
        time.sleep(DELAY * 2)
    print(cyan(bold("OMNI AGENT - offline demo (real tools, scripted decisions)")))
    goal = "Explore why cancer drugs stop working, test an unconventional idea, and report only what holds up."
    print(yellow("goal> ") + goal + "\n")
    answer = Agent(ScriptedModel(), ws, auto_approve=True, max_steps=14, state=state).run(goal)
    print(bold("\n=========== FINAL ANSWER ===========\n") + answer)
    if "--no-dashboard" not in sys.argv:
        if "--no-wait" in sys.argv:
            time.sleep(float(os.environ.get("DEMO_HOLD", "0")))
        else:
            input("\nPress Enter to close the dashboard...")
