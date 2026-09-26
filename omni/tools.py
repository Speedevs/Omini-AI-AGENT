"""
Everything the agent can DO lives here. Each tool = a JSON schema the model sees + a Python method that runs it.
Adding a new ability is just adding a new entry to SPECS and a matching method.
"""
import html
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

from . import config
from . import desktop
from concurrent.futures import ThreadPoolExecutor

from .evidence import (CLOSE, UNREACHABLE, VERIFIED, check_quote, corroboration_score, domain, excerpt)
from .ui import ask_permission, dim

MAX_OUTPUT = 12000  # characters of tool output fed back to the model

SPECS = [
    {"name": "think",
     "description": "Private scratchpad. Use it to reason step by step, weigh hypotheses, question assumptions, or brainstorm unconventional approaches before acting. Nothing happens in the world.",
     "schema": {"type": "object", "properties": {"thought": {"type": "string"}}, "required": ["thought"]}},
    {"name": "update_plan",
     "description": "Write or revise your plan as a list of steps, each with a status (todo, doing, done, dropped). Call it at the start and whenever the plan changes.",
     "schema": {"type": "object", "properties": {"steps": {"type": "array", "items": {"type": "object", "properties": {
         "step": {"type": "string"}, "status": {"type": "string", "enum": ["todo", "doing", "done", "dropped"]}},
         "required": ["step", "status"]}}}, "required": ["steps"]}},
    {"name": "run_shell",
     "description": "Run a shell command in the workspace folder (cmd.exe on Windows, sh/bash on Linux/macOS). Returns stdout, stderr and exit code.",
     "schema": {"type": "object", "properties": {"command": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds, default 120"}}, "required": ["command"]}},
    {"name": "run_python",
     "description": "Run a Python script in the workspace folder and return its output. Good for math, data analysis, simulations, and quick experiments. Print what you want to see.",
     "schema": {"type": "object", "properties": {"code": {"type": "string"},
                "timeout": {"type": "integer", "description": "Seconds, default 120"}}, "required": ["code"]}},
    {"name": "read_file",
     "description": "Read a text file inside the workspace. Optionally give start_line/end_line for big files.",
     "schema": {"type": "object", "properties": {"path": {"type": "string"},
                "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file",
     "description": "Create or overwrite a text file inside the workspace (folders are created automatically).",
     "schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"]}},
    {"name": "list_dir",
     "description": "List files and folders inside the workspace (relative path, default '.').",
     "schema": {"type": "object", "properties": {"path": {"type": "string"}}}},
    {"name": "web_search",
     "description": "Search the web. Returns titles, URLs and snippets. Follow up with fetch_url to read a page.",
     "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "fetch_url",
     "description": "Download a web page (or plain-text/JSON API response) and return readable text. Long pages: "
                    "pass `find` (words to look for) to get just the matching passages, or `start` (character "
                    "offset) to read further in.",
     "schema": {"type": "object", "properties": {"url": {"type": "string"},
                "find": {"type": "string", "description": "e.g. 'G12D pancreatic' returns passages containing these"},
                "start": {"type": "integer", "description": "character offset to start reading from"}},
                "required": ["url"]}},
    {"name": "remember",
     "description": "Save a fact, lesson or result to long-term memory so future runs can use it.",
     "schema": {"type": "object", "properties": {"topic": {"type": "string"}, "note": {"type": "string"}},
                "required": ["topic", "note"]}},
    {"name": "recall",
     "description": "Search long-term memory from previous runs by keywords.",
     "schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "delegate",
     "description": "Hand a well-defined sub-task to a fresh sub-agent with its own clean context. Use for independent chunks of work (e.g. 'research topic X and summarize with sources'). Returns its final answer.",
     "schema": {"type": "object", "properties": {"task": {"type": "string"}}, "required": ["task"]}},
    {"name": "record_claim",
     "description": "Register an important factual claim in the Evidence Ledger. Give the source (a URL, or 'workspace:<path>' "
                    "for a file you produced) and the EXACT quote from that source that supports it. Omni will independently "
                    "load the source and check the quote is really there, then stamp the claim verified / close match / "
                    "not found. Register every key fact, number or citation your final answer depends on.",
     "schema": {"type": "object", "properties": {"claim": {"type": "string"}, "source": {"type": "string"},
                "quote": {"type": "string", "description": "Copied word-for-word from the source"}},
                "required": ["claim", "source", "quote"]}},
    {"name": "retract_claim",
     "description": "Withdraw a claim from the Evidence Ledger (e.g. after it came back NOT FOUND). Give its number.",
     "schema": {"type": "object", "properties": {"number": {"type": "integer"}, "reason": {"type": "string"}},
                "required": ["number", "reason"]}},
    {"name": "convene_council",
     "description": "Put a hard question to a council of three independent minds that think in parallel: the Skeptic "
                    "(finds flaws and hidden assumptions), the Inventor (cross-domain, unconventional ideas) and the "
                    "Pragmatist (cheapest concrete next step). Use it at key decision points, then synthesize their views.",
     "schema": {"type": "object", "properties": {"question": {"type": "string"},
                "context": {"type": "string", "description": "What you know so far that they need"}},
                "required": ["question"]}},
    {"name": "browser",
     "description": "Control a real, visible browser window. Actions: open (url), read (page text + numbered elements), "
                    "click (element number), type (element number + text, optional submit), scroll, back, screenshot, "
                    "close. Use element numbers from the latest page view. It won't type passwords, recovery phrases "
                    "or private keys, and important buttons (pay, send, sign, delete...) need the user's OK.",
     "schema": {"type": "object", "properties": {
         "action": {"type": "string", "enum": ["open", "read", "click", "type", "scroll", "back", "screenshot", "close"]},
         "url": {"type": "string"}, "element": {"type": "integer"}, "text": {"type": "string"},
         "submit": {"type": "boolean"}}, "required": ["action"]}},
    {"name": "screen",
     "description": "Capture the user's screen: action 'screenshot' saves a PNG; action 'record' records the screen "
                    "for `seconds` (max 300) and saves an MP4. Files go to workspace/captures and appear on the dashboard. "
                    "Always asks the user first.",
     "schema": {"type": "object", "properties": {"action": {"type": "string", "enum": ["screenshot", "record"]},
                "seconds": {"type": "number"}}, "required": ["action"]}},
    {"name": "launch_app",
     "description": "Open an application, file or URL on the user's computer without waiting for it to close. "
                    "Examples: 'chrome', 'chrome https://example.com', 'firefox', 'opera', 'https://example.com', a file path.",
     "schema": {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}},
    {"name": "install_app",
     "description": "Install an app with the OS package manager from Omni's vetted catalog (chrome, firefox, opera, "
                    "opera gx, brave, edge, vlc, vscode, telegram, obs, 7zip, git, ffmpeg), or an exact ID like "
                    "'winget:Publisher.App'. Browser extensions phantom, rabby and frontrun (Frontrun Pro) are handled "
                    "specially: Omni opens only their official store page and the user clicks install. NEVER look for "
                    "wallet or extension downloads via search.",
     "schema": {"type": "object", "properties": {"name": {"type": "string"},
                "browser": {"type": "string", "description": "For extensions: which browser (chrome, brave, edge, firefox)"}},
                "required": ["name"]}},
    {"name": "uninstall_app",
     "description": "Uninstall an app with the OS package manager (same catalog and ID forms as install_app). "
                    "Browser extensions can't be removed by programs: use open_extensions_page instead.",
     "schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}},
    {"name": "open_extensions_page",
     "description": "Help the user remove or check extensions in a browser (chrome, brave, edge, firefox). Pass the "
                    "extension names if known: for vetted ones Omni opens their store page, which has a Remove button.",
     "schema": {"type": "object", "properties": {"browser": {"type": "string"},
                "names": {"type": "array", "items": {"type": "string"}}}, "required": ["browser"]}},
    {"name": "setup_firefox",
     "description": "Configure Firefox by writing its user.js settings file. Options: homepage (url), "
                    "restore_session, strict_tracking_protection, https_only, telemetry_off, ask_where_to_save, "
                    "dark_theme, block_popups (booleans). Works on a fresh install too. Applies when Firefox (re)starts.",
     "schema": {"type": "object", "properties": {"settings": {"type": "object"}}, "required": ["settings"]}},
    {"name": "send_file",
     "description": "Deliver a file from the workspace to the user: it is always shown on the dashboard, and sent to "
                    "the user's own Telegram chat if they configured it. Cannot send to anyone else.",
     "schema": {"type": "object", "properties": {"path": {"type": "string"}, "caption": {"type": "string"}},
                "required": ["path"]}},
    {"name": "ask_user",
     "description": "Pause and ask the user a short question when you truly need their input (a choice only they "
                    "can make, or missing information). Don't ask about things you can find out yourself.",
     "schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "final_answer",
     "description": "Finish the task and give the user your final answer. Be honest about what was verified vs. uncertain.",
     "schema": {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"]}},
]


def _clip(text: str) -> str:
    if len(text) <= MAX_OUTPUT:
        return text
    half = MAX_OUTPUT // 2
    return text[:half] + f"\n\n...[{len(text) - MAX_OUTPUT} characters cut]...\n\n" + text[-half:]


class Toolbox:
    def __init__(self, workspace: Path, memory, auto_approve=False, depth=0, spawn=None, state=None, council=None,
                 judge=None, receipt=None):
        self.judge = judge          # blind judge function (set by the Agent)
        self.receipt = receipt      # tamper-evident receipt (snapshots sources)
        self.state = state
        from .undo import Journal
        self.journal = Journal(workspace)  # how to reverse every change (python -m omni.undo)
        self.council = council      # function that runs the council (set by the Agent)
        self.ws = workspace
        self.memory = memory
        self.auto = auto_approve
        self.depth = depth
        self.spawn = spawn          # function that runs a sub-agent (set by the Agent)
        self.plan = []

    def specs(self):
        # Sub-agents can't spawn more sub-agents (stops runaway recursion).
        return [s for s in SPECS if not (s["name"] == "delegate" and self.depth >= 1)]

    def run(self, name, args):
        fn = getattr(self, f"t_{name}", None)
        if fn is None:
            return f"Error: unknown tool '{name}'."
        try:
            return _clip(str(fn(**args)))
        except TypeError as e:
            return f"Error: bad arguments for {name}: {e}"
        except Exception as e:  # tools must never crash the agent; report the error back instead
            return f"Error in {name}: {type(e).__name__}: {e}"

    # ---------- helpers ----------
    def _path(self, p: str) -> Path:
        full = (self.ws / p).resolve()
        if config.get("OMNI_ALLOW_OUTSIDE_WORKSPACE") != "1" and not full.is_relative_to(self.ws.resolve()):
            raise PermissionError(f"'{p}' is outside the workspace. Set OMNI_ALLOW_OUTSIDE_WORKSPACE=1 to allow.")
        return full

    def _ok(self, action, detail):
        return self.auto or ask_permission(action, detail)

    def _exec(self, cmd, timeout, shell):
        r = subprocess.run(cmd, shell=shell, cwd=self.ws, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           encoding="utf-8", errors="replace", timeout=timeout or 120)
        return f"exit code: {r.returncode}\n--- stdout ---\n{r.stdout}\n--- stderr ---\n{r.stderr}"

    # ---------- tools ----------
    def t_think(self, thought):
        return "Noted. Continue."

    def t_update_plan(self, steps):
        self.plan = steps
        if self.state and self.depth == 0:
            self.state.set(plan=steps)
        marks = {"todo": "[ ]", "doing": "[>]", "done": "[x]", "dropped": "[-]"}
        text = "\n".join(f"{marks.get(s.get('status'), '[ ]')} {s.get('step')}" for s in steps)
        print(dim(text))
        return "Plan saved:\n" + text

    def t_run_shell(self, command, timeout=120):
        if not self._ok("run shell command", command):
            return "User denied this command. Try another approach or ask via final_answer."
        return self._exec(command, timeout, shell=True)

    def t_run_python(self, code, timeout=120):
        if not self._ok("run Python code", code):
            return "User denied running this code."
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, dir=self.ws, encoding="utf-8") as f:
            f.write(code)
            path = f.name
        try:
            return self._exec([sys.executable, path], timeout, shell=False)
        finally:
            os.remove(path)

    def t_read_file(self, path, start_line=None, end_line=None):
        lines = self._path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        s = (start_line or 1) - 1
        e = end_line or len(lines)
        return "\n".join(f"{i + 1}: {l}" for i, l in enumerate(lines[s:e], start=s)) or "(empty file)"

    def t_write_file(self, path, content):
        if desktop.contains_secret(content):
            return ("BLOCKED: this content looks like a wallet recovery phrase or private key. Omni never writes "
                    "these to files: saved seed phrases are what crypto-stealing malware searches for. The owner "
                    "should write it on paper, offline.")
        p = self._path(path)
        if p.exists() and not self._ok("overwrite file", str(p)):
            return "User denied overwriting this file."
        p.parent.mkdir(parents=True, exist_ok=True)
        self.journal.before_file_change(p, f"{'Overwrote' if p.exists() else 'Created'} file {path}")
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} characters to {path}"

    def t_list_dir(self, path="."):
        p = self._path(path)
        items = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        return "\n".join(f"{'[dir] ' if i.is_dir() else ''}{i.name}" for i in items) or "(empty)"

    def t_web_search(self, query):
        if config.get("TAVILY_API_KEY"):
            r = requests.post("https://api.tavily.com/search", timeout=30,
                              json={"api_key": config.get("TAVILY_API_KEY"), "query": query, "max_results": 8})
            r.raise_for_status()
            return "\n\n".join(f"{x['title']}\n{x['url']}\n{x.get('content', '')[:400]}" for x in r.json()["results"])
        if config.get("BRAVE_API_KEY"):
            r = requests.get("https://api.search.brave.com/res/v1/web/search", timeout=30, params={"q": query},
                             headers={"X-Subscription-Token": config.get("BRAVE_API_KEY")})
            r.raise_for_status()
            res = r.json().get("web", {}).get("results", [])
            return "\n\n".join(f"{x['title']}\n{x['url']}\n{x.get('description', '')}" for x in res[:8])
        # Free fallback: DuckDuckGo's HTML page (no key, but can be rate-limited).
        r = requests.post("https://html.duckduckgo.com/html/", data={"q": query}, timeout=30,
                          headers={"User-Agent": "Mozilla/5.0"})
        hits = re.findall(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
                          r.text, re.S)
        if not hits:
            return "No results (free search may be rate-limited; add TAVILY_API_KEY or BRAVE_API_KEY to .env)."
        clean = lambda s: html.unescape(re.sub("<[^>]+>", "", s)).strip()
        out = []
        for url, title, snip in hits[:8]:
            m = re.search(r"uddg=([^&]+)", url)
            if m:
                url = requests.utils.unquote(m.group(1))
            out.append(f"{clean(title)}\n{url}\n{clean(snip)}")
        return "\n\n".join(out)

    def t_fetch_url(self, url, find=None, start=0):
        text = self._fetch_text(url)
        total = len(text)
        if find:
            words = [w.lower() for w in re.findall(r"\w+", find) if len(w) > 1]
            hits, last_end = [], -1
            low = text.lower()
            for m in re.finditer(re.escape(words[0]), low) if words else []:
                a, b = max(0, m.start() - 500), min(total, m.end() + 500)
                window = low[a:b]
                if a > last_end and all(w in window for w in words):
                    hits.append(f"[chars {a}-{b}]\n{text[a:b]}")
                    last_end = b
                if len(hits) == 8:
                    break
            return (f"{len(hits)} passage(s) containing {words} in {total} chars:\n\n" + "\n\n---\n\n".join(hits)) \
                if hits else f"No passage contains all of {words}. Try fewer or different words."
        chunk = text[start:start + MAX_OUTPUT - 300]
        more = f"\n\n[showing chars {start}-{start + len(chunk)} of {total}. Use start={start + len(chunk)} to " \
               f"read on, or find='keywords' to jump to what you need]" if start + len(chunk) < total else ""
        return chunk + more

    def _fetch_text(self, url):
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (OmniAgent research bot)"})
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        if "html" not in ctype:
            return r.text
        t = re.sub(r"(?is)<(script|style|noscript|svg|nav|footer|header).*?</\1>", " ", r.text)
        t = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h\d>|</li>", "\n", t)
        t = html.unescape(re.sub(r"<[^>]+>", " ", t))
        t = re.sub(r"[ \t]+", " ", t)
        return re.sub(r"\n\s*\n+", "\n\n", t).strip()

    def t_remember(self, topic, note):
        self.memory.add(topic, note)
        return f"Saved to memory under '{topic}'."

    def t_recall(self, query):
        return self.memory.search(query)

    def t_delegate(self, task):
        if not self.spawn:
            return "Delegation not available here."
        return self.spawn(task)

    def _search_urls(self, query):
        return re.findall(r"^(https?://\S+)$", self.t_web_search(query), re.M)

    def _corroborate(self, claim, source):
        """Code-only check: do OTHER websites (different domains) contain this claim's numbers and key terms?"""
        if config.get("OMNI_CORROBORATE", "1") == "0" or not source.startswith("http"):
            return None, []
        try:
            urls, seen = [], {domain(source)}
            for u in self._search_urls(claim[:160]):
                d = domain(u)
                if d not in seen:
                    seen.add(d)
                    urls.append(u)
                if len(urls) == 3:
                    break
            if not urls:
                return None, []  # search unavailable (e.g. rate-limited): report nothing rather than "0 of 0"

            def score(u):
                try:
                    sc, passage = corroboration_score(claim, self._fetch_text(u))
                    return {"url": u, "domain": domain(u), "score": sc, "passage": passage[:700]}
                except Exception:
                    return {"url": u, "domain": domain(u), "score": 0, "passage": ""}
            with ThreadPoolExecutor(max_workers=3) as pool:
                checked = list(pool.map(score, urls))
            # Keyword matches are only candidates; the blind judge decides whether each passage really agrees.
            for c in checked:
                if c["score"] >= 80 and self.judge:
                    v = self.judge(claim, c["passage"])
                    c["judge"], c["judge_reason"] = v.get("verdict"), v.get("reason", "")
                c["confirms"] = c["score"] >= 80 and (c.get("judge") == "supports" or not self.judge)
            return sum(1 for c in checked if c["confirms"]), checked
        except Exception:
            return None, []

    def t_record_claim(self, claim, source, quote):
        text, snap = "", None
        try:
            if source.startswith("workspace:"):
                text = self._path(source[len("workspace:"):].strip()).read_text(encoding="utf-8", errors="replace")
            else:
                text = self._fetch_text(source)
            status, pct = check_quote(quote, text)
            if self.receipt:
                snap = self.receipt.snapshot(text)  # keep the exact evidence, even if the page changes later
        except Exception:
            status, pct = UNREACHABLE, 0
        item = {"claim": claim, "source": source, "quote": quote[:400], "status": status, "match": pct,
                "snapshot": snap}
        lines = [f"Quote check (by code): {status.upper()} ({pct}% match)."]
        if status in (VERIFIED, CLOSE):
            passage = excerpt(quote, text)
            item["excerpt"] = passage[:900]
            if self.judge and passage:
                v = self.judge(claim, passage)
                if v.get("verdict"):
                    item["judge"], item["judge_reason"] = v["verdict"], v.get("reason", "")
                    lines.append(f"Blind judge (saw only your claim + the source passage): {item['judge'].upper()}. "
                                 f"{item['judge_reason']}")
                else:
                    lines.append("Blind judge unavailable for this claim (no valid verdict).")
            n, checked = self._corroborate(claim, source)
            if n is not None:
                item["corroborated"], item["corroborating"] = n, checked
                lines.append(f"Corroboration: {n} of {len(checked)} other sites confirm it (a passage with the same "
                             f"key facts, which the blind judge agreed supports the claim).")
        num = self.state.add("claims", item) + 1
        self.state.event("claim", f"#{num} {status.upper()}"
                         + (f", judge: {item['judge']}" if item.get("judge") else "")
                         + (f", {item['corroborated']} other sites confirm" if item.get("corroborated") is not None else "")
                         + f": {claim}", self.depth)
        if item.get("judge") in ("contradicts", "unrelated"):
            lines.append("The quote is real, but it does NOT support your claim as written. Reword the claim to "
                         "match the source exactly, or retract_claim it.")
        elif item.get("judge") == "partial":
            lines.append("The claim overstates the source. Narrow the wording to what the passage says.")
        elif status == "not found":
            lines.append("The source does NOT contain this quote. Find real support, or retract_claim it and "
                         "leave it out of your answer.")
        elif status == UNREACHABLE:
            lines.append("The source could not be loaded; find another source.")
        return f"Claim #{num}. " + " ".join(lines) + (f" Snapshot saved (SHA-256 {snap[:12]}...)." if snap else "")

    def t_retract_claim(self, number, reason):
        claims = self.state.snapshot()["claims"]
        if not 1 <= number <= len(claims):
            return f"No claim #{number}."
        self.state.update_item("claims", number - 1, status="retracted", reason=reason, was=claims[number - 1]["status"])
        self.state.event("claim", f"#{number} RETRACTED: {reason}", self.depth)
        return f"Claim #{number} retracted. Make sure your final answer does not rely on it."

    def t_convene_council(self, question, context=""):
        if not self.council:
            return "Council not available here."
        return self.council(question, context)

    # ---------- desktop powers (see desktop.py) ----------
    def _new_file(self, folder, ext):
        d = self.ws / folder
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{time.strftime('%Y%m%d_%H%M%S')}{ext}"

    def _publish(self, path: Path, label):
        rel = path.relative_to(self.ws).as_posix()
        self.state.add("files", {"path": rel, "label": label, "time": time.strftime("%H:%M:%S")})
        return rel

    def t_browser(self, action, url=None, element=None, text=None, submit=False):
        path = self._new_file("captures", "_browser.png") if action == "screenshot" else None
        out = desktop.get_browser(self.ws).act(action, url, element, text, submit, path, self.auto)
        if path and path.exists():
            out += f"\n(workspace path: {self._publish(path, 'Browser screenshot')})"
        return out

    def t_screen(self, action, seconds=5):
        if action == "record":
            if not ask_permission("record your screen", f"{seconds} seconds. It captures everything visible."):
                return "User declined screen recording."
            path = self._new_file("captures", "_screen.mp4")
            msg = desktop.record(path, seconds)
        else:
            if not ask_permission("take a screenshot of your screen", "Captures everything visible."):
                return "User declined the screenshot."
            path = self._new_file("captures", "_screen.png")
            msg = desktop.screenshot(path)
        return msg + f"\n(workspace path: {self._publish(path, 'Screen recording' if action == 'record' else 'Screenshot')})"

    def t_launch_app(self, target):
        if not self._ok("open an app", target):
            return "User declined."
        return desktop.launch(target)

    def t_install_app(self, name, browser=""):
        out = desktop.install(name, self.ws, self.auto, browser)
        if out.startswith("exit code 0"):
            self.journal.record(f"Installed {name}", {"type": "uninstall", "name": name})
        elif out.startswith("Opened") and "Chrome Web Store" in out:
            opened_in = re.search(r"Web Store page in (\w+)", out)
            self.journal.record(f"Opened the store page for the {name} extension (if you added it)",
                                {"type": "extensions_page", "browser": opened_in.group(1) if opened_in else "chrome",
                                 "name": name})
        return out

    def t_uninstall_app(self, name):
        return desktop.uninstall(name, self.auto)

    def t_open_extensions_page(self, browser, names=None):
        if not self._ok("open the extensions page", browser):
            return "User declined."
        return desktop.open_extensions_page(browser, [str(n).lower().replace(" pro", "") for n in (names or [])])

    def t_setup_firefox(self, settings):
        if not self._ok("change Firefox settings", json.dumps(settings)):
            return "User declined."
        return desktop.setup_firefox(settings, lambda f: self.journal.before_file_change(
            f, f"Changed Firefox settings in {f.parent.name}/user.js"))

    def t_send_file(self, path, caption=""):
        p = self._path(path)
        if not p.is_file():
            return f"No file at {path}."
        if p.stat().st_size < 5_000_000 and desktop.contains_secret(p.read_text(encoding="utf-8", errors="ignore")):
            return "BLOCKED: this file appears to contain a recovery phrase or private key. Omni won't send it anywhere."
        rel = p.relative_to(self.ws.resolve()).as_posix()
        if not any(f["path"] == rel for f in self.state.snapshot()["files"]):
            self.state.add("files", {"path": rel, "label": caption or p.name, "time": time.strftime("%H:%M:%S")})
        return f"File ready: {p}\n" + desktop.send_telegram(p, caption or p.name)

    def t_ask_user(self, question):
        self.state.event("ask", question, self.depth)
        self.state.set(question=question)
        if not sys.stdin or not sys.stdin.isatty():
            self.state.set(question="")
            return "The user isn't available to answer right now. Make the most sensible choice and say which you made."
        from .ui import cyan
        print(cyan(f"\n  Omni asks: {question}"))
        try:
            answer = input(cyan("  your answer> ")).strip()
        except EOFError:
            answer = ""
        self.state.set(question="")
        self.state.event("answer", answer or "(no answer)", self.depth)
        return f"User answered: {answer}" if answer else "The user didn't answer. Use your best judgment."

    def t_final_answer(self, answer):
        return answer


def system_info():
    return f"{platform.system()} {platform.release()}, Python {sys.version.split()[0]}"
