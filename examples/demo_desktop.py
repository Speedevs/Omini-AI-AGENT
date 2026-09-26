"""
Offline demo of Omni's desktop powers, no API key needed.
Real: the browser window, Wikipedia, typing and searching, the 2-second screen recording, file delivery,
the official wallet page, and the safety block. Scripted: the AI's decisions.

    python examples/demo_desktop.py
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from omni import dashboard, desktop, tools, ui  # noqa: E402
from omni.agent import Agent  # noqa: E402
from omni.state import MissionState  # noqa: E402

DELAY = float(os.environ.get("DEMO_DELAY", "1.4"))
SEED = "abandon ability able about above absent absorb abstract absurd abuse access accident"  # public test words


def demo_yes(action, detail):
    """Permission prompts are answered 'y' automatically in this demo (shown on screen)."""
    print(ui.yellow(f"\n  The agent wants to {action}:"))
    print(ui.yellow("  | ") + detail.splitlines()[0][:100])
    print(ui.yellow("  Allow? [y]es / [n]o / [a]lways this session: ") + "y   (auto-answered for this demo)")
    time.sleep(DELAY * 0.8)
    return True


ui.ask_permission = desktop.ask_permission = tools.ask_permission = demo_yes


def call(tool, **a):
    return {"id": f"{tool}_{time.time_ns()}", "name": tool, "input": a}


def last_result(messages, tool):
    for m in reversed(messages):
        if m["role"] == "tool" and m["name"] == tool:
            return m["content"]
    return ""


class ScriptedModel:
    model = "scripted demo model (no API key)"

    def chat(self, system, messages, tools_):
        time.sleep(DELAY)
        if system.startswith("You are a concise reviewer"):
            return {"content": '["Use the browser tool for pages that need typing; fetch_url is faster for reading."]'}
        n = sum(1 for m in messages if m["role"] == "assistant")
        if n == 0:
            return {"content": "", "tool_calls": [call("update_plan", steps=[
                {"step": "Look up Solana on Wikipedia in the browser", "status": "doing"},
                {"step": "Record the screen for 2 seconds and send the file", "status": "todo"},
                {"step": "Open the official Phantom wallet page", "status": "todo"}]),
                call("browser", action="open", url="https://en.wikipedia.org/wiki/Special:Search")]}
        if n == 1:
            page = last_result(messages, "browser")
            box = next((int(x) for x, kind, label in re.findall(r"\[(\d+)\] (input:search|input:text) ?(.*)", page)
                        if "search" in label.lower() or kind == "input:search"), 1)
            return {"content": f"Found the search box as element {box}.",
                    "tool_calls": [call("browser", action="type", element=box, text="Solana blockchain", submit=True)]}
        if n == 2:
            return {"content": "Page loaded. Now recording 2 seconds of the screen.",
                    "tool_calls": [call("update_plan", steps=[
                        {"step": "Look up Solana on Wikipedia in the browser", "status": "done"},
                        {"step": "Record the screen for 2 seconds and send the file", "status": "doing"},
                        {"step": "Open the official Phantom wallet page", "status": "todo"}]),
                        call("screen", action="record", seconds=2)]}
        if n == 3:
            path = re.search(r"workspace path: (\S+)\)", last_result(messages, "screen")).group(1)
            return {"content": "", "tool_calls": [call("update_plan", steps=[
                {"step": "Look up Solana on Wikipedia in the browser", "status": "done"},
                {"step": "Record the screen for 2 seconds and send the file", "status": "done"},
                {"step": "Open the official Phantom wallet page", "status": "doing"}]),
                call("send_file", path=path, caption="Your 2-second screen recording")]}
        if n == 4:
            return {"content": "Opening Phantom only through Omni's hardcoded official link.",
                    "tool_calls": [call("install_app", name="phantom")]}
        if n == 5:
            page = last_result(messages, "browser")
            box = next((int(x) for x, kind in re.findall(r"\[(\d+)\] (input:search|input:text)", page)), 1)
            return {"content": "Safety test: trying to type a sample 12-word recovery phrase (must be blocked).",
                    "tool_calls": [call("update_plan", steps=[
                        {"step": "Look up Solana on Wikipedia in the browser", "status": "done"},
                        {"step": "Record the screen for 2 seconds and send the file", "status": "done"},
                        {"step": "Open the official Phantom wallet page", "status": "done"}]),
                        call("browser", action="type", element=box, text=SEED)]}
        return {"content": "", "tool_calls": [call("final_answer", answer=
                "Done. I looked up Solana on Wikipedia, recorded 2 seconds of your screen and delivered the MP4 "
                "(see Files on the dashboard), and opened Phantom's official Chrome Web Store page for you to "
                "install yourself. The recovery-phrase test was blocked, as it should be.")]}


if __name__ == "__main__":
    ws = Path(tempfile.mkdtemp(prefix="omni_desktop_demo_"))
    state = MissionState()
    print(ui.cyan(f"Mission control: {dashboard.start(state, int(os.environ.get('DEMO_PORT', '8765')), '--no-browser' not in sys.argv, ws)}"))
    time.sleep(DELAY)
    goal = ("Look up Solana on Wikipedia, record my screen for 2 seconds and send me the file, "
            "then get me the Phantom wallet.")
    print(ui.cyan(ui.bold("OMNI - desktop powers demo (real browser and screen, scripted decisions)")))
    print(ui.yellow("goal> ") + goal + "\n")
    answer = Agent(ScriptedModel(), ws, auto_approve=False, max_steps=12, state=state).run(goal)
    print(ui.bold("\n=========== FINAL ANSWER ===========\n") + answer)
    time.sleep(float(os.environ.get("DEMO_HOLD", "0")))
