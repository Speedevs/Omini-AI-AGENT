# Omni Agent

A general-purpose autonomous AI agent that runs on **Windows, Linux and macOS**.
Give it a goal in plain English; it plans, searches the web, reads sources, writes and runs code,
creates files, checks its own work, delegates sub-tasks to helper agents, and remembers what it
learned for next time.

It is not AGI (nothing is, yet). It is a readable, hackable agent whose answers come with proof you can
verify yourself, plus desktop powers and a live dashboard, described below.

**Tested for real:** besides the offline demos in `examples/`, Omni has run real missions end to end on Kimi
K2.6: research with proof, a TypeScript coding task, installing and setting up Firefox, and opening wallet and
extension pages followed by an undo. Those real runs exposed about a dozen bugs the scripted demos never
showed, and all of them are fixed. Other providers (Claude, Grok, OpenAI, OpenRouter, Ollama) use the same code
paths, but only Kimi has been tested live so far.

**Try it with no API key first:** `python examples/demo_proof.py` shows proof-carrying answers and the
tamper tests; `python examples/demo_offline.py` runs a full mission with scripted
AI decisions but real tools, real web access, real claim checks and the live dashboard.

## What's unique: proof-carrying answers

Every other AI agent asks you to trust its answer. Omni hands you **proof you can check without trusting
Omni or any AI**, today or years later, even if the websites it used change or disappear.

Every mission ends with a `missions/<time>/` folder containing:
- `proof.html`: the answer with every sentence marked. Green means backed by checked evidence, purple
  dashed means labeled as speculation, and red means not backed. Click a sentence to see its evidence,
  with the exact source passage highlighted.
- `receipt.json`: every step of the mission (AI replies, tool calls, results) in a hash chain, the same
  idea a blockchain uses.
- `snapshots/`: the exact source text each claim was checked against, named by its SHA-256 fingerprint.

How an answer earns its proof:
1. **Code quote check.** Omni downloads the source and confirms the quoted words are really there.
2. **Blind judge.** A fresh AI call sees only the claim and the passage that *code* cut from the source,
   never the agent's reasoning, and rules supports / partial / contradicts / unrelated. This catches the
   subtle lie other checks miss: a real quote attached to a claim it doesn't support. (In the demo:
   "sotorasib is approved for pancreatic cancer," backed by a real sentence about *lung* cancer.)
3. **Independent confirmation.** Code searches other websites and finds passages with the claim's
   numbers and key terms *together*. The blind judge then reads each one, and a site only counts if the
   judge agrees it supports the claim. (Keyword matching alone is fooled: a page saying sotorasib is
   approved "in lung and colorectal cancer" matches every keyword of the false claim.)
4. **Grounding gate.** Before the answer is accepted, code checks it sentence by sentence. Any sentence
   with a number that no verified claim backs, or that repeats a rejected claim, is sent back to the
   agent. If it survives a second try, it's printed with `[unbacked]` after it.
5. **Seal.** The final answer and evidence are hashed into the chain. The final "root" hash is sent to
   your Telegram (if set up) so you hold a copy outside the receipt.

Check any receipt yourself, offline:
```
python -m omni.verify missions/<time>/receipt.json --root <root hash you kept>
```
It re-computes every hash, re-runs every quote check against the saved snapshots, re-grounds every
sentence, and compares the root. Change one number anywhere and it fails. Rebuild every hash to
cover the change and it still fails, because the root no longer matches your copy.
Run `python examples/demo_proof.py` to watch both tamper attempts get caught.

**What the proof does NOT prove (read this):** that a source is right, only that it says what Omni
claims it says. The blind judge is an AI and can be wrong; its verdicts are recorded so you can review
them. The grounding gate checks sentences with numbers strictly, but general sentences only loosely.

## Undo: every change can be reversed

Omni logs how to reverse every change it makes to your computer: files it created or overwrote, Firefox
settings, apps it installed, and extension pages it opened. Nothing else in this space gives you that.
```
python -m omni.undo --list          # see what each mission changed
python -m omni.undo                 # reverse the last mission (asks first)
```
Browsers don't let any program remove extensions, so for those Omni opens the extension's official store
page, which shows a "Remove" button when it's installed.

## Play chess with Omni while it works

The dashboard has a "Play while I work" board. Make a move and Omni answers in the background, even in the
middle of a task, explaining each move in one sentence. Same rule as everything else here: **the AI proposes,
code verifies.** The python-chess rules engine checks every move, so Omni can't cheat. If the AI ever proposes
an illegal move, the dashboard shows it was caught; after two misses a simple code fallback plays instead,
clearly labelled.

Fast reaction games are a different story. Omni can open Subway Surfers for you (only SYBO's official web
version on poki.com, never look-alike "unblocked" copies), but an AI that needs seconds per decision can't play
it. You play that one.

## Companion mode: it starts the conversation

```
python -m omni --companion          # greets you, then checks in after 10 quiet minutes
python -m omni --companion 30       # check in after 30 minutes instead
```
Omni opens with something relevant (a follow-up on your last mission, a next step, or a question), checks in when
you've been quiet, and also messages your Telegram if you set it up. It stops after 3 check-ins you don't answer.
During missions, the agent can also stop and ask you a question (`ask_user`) when only you can decide.

## Several API keys

- **Backups:** `OMNI_FALLBACK=kimi,grok,anthropic` in `.env`. If the main provider fails (no credit, bad key,
  outage), Omni switches to the next one and keeps working.
- **Independent judge:** `OMNI_JUDGE_PROVIDER=grok` makes a different company's AI check the agent's claims.
- **Cheaper reflection:** `OMNI_REFLECT_PROVIDER` / `OMNI_REFLECT_MODEL`.

## Step budget

Three steps before its limit, the agent is told to stop researching, finish any quick actions you asked for,
and answer with what it has. If it still runs out, a short summary is written from a trimmed history, and if
the AI service doesn't respond, Omni still returns what it verified. You always get an answer.

## Background reflection (its "inner voice")

Every 3 minutes (set `OMNI_REFLECT_EVERY` in `.env`, 0 = off) a separate thread reviews the mission **in
parallel while the agent keeps working**, even during a long download, command or recording. It reports
PROGRESS / RISK / BLIND SPOT / NEXT. The agent receives it at its next step, and it appears on the
dashboard and is sealed into the receipt. A code-level stuck detector triggers a reflection immediately if the
agent repeats the same action three times.

## Also in Omni

**1. Evidence Ledger: claims are checked by code, not by the AI.**
AI models sometimes invent quotes and statistics, and telling them to "be careful" doesn't reliably stop it.
Omni makes the agent register key facts with `record_claim` (claim + source + exact quote). Omni then loads
the source itself and searches it for the quote. The AI plays no part in this check. Each claim gets a stamp:
verified, close match, not found, or unreachable. Caught mistakes can be retracted. Every final answer ends
with an audit and a trust score. In the demo, the agent "remembers" that KRAS G12D is in over 90% of
pancreatic cancers; the ledger finds the source actually says up to 37%, stamps it NOT FOUND, and the claim
is retracted before it reaches the answer.

**2. Council of three: opposed thinkers in parallel.**
At key decisions the agent calls `convene_council`. Three independent model calls run at the same time as
a Skeptic (finds the weak assumption), an Inventor (ideas from other fields) and a Pragmatist (cheapest next
step). The agent must weigh their disagreement before acting.

**3. Evolving playbook: it gets better at *working*, not just at remembering facts.**
After each mission Omni runs a retrospective and writes 1-3 lessons about method to
`workspace/.omni/playbook.md` ("fetch known reference pages directly when search is rate-limited").
Those lessons go into the instructions of every future mission. You can read and edit the file.

**4. Mission Control: a live dashboard.**
Each run opens http://localhost:8765 showing the plan, every action, sub-agents, council debates, and
the evidence ledger with its stamps updating live. It works in light and dark mode and on a phone-sized
window. It only listens on your own computer. Use `--no-dashboard` to turn it off.

## Desktop powers

Omni can also work your computer, not just the terminal:

- **Browser control**: it opens a real, visible browser window, reads pages, clicks, types and searches.
  It uses your installed Chrome if you have it, with a separate profile, so your own logins and bookmarks
  are untouched.
- **Screen capture**: "record my screen for 2 seconds and send me the file" produces an MP4 in
  `workspace/captures/`, shows it on the dashboard with a player, and (optionally) sends it to your
  Telegram. Screenshots work the same way.
- **Open apps**: `launch_app` starts Chrome, Firefox, Opera or any file/URL without freezing the agent.
- **Install apps**: `install_app` uses your OS package manager (winget on Windows, Homebrew on macOS,
  Flatpak on Linux) with a vetted catalog: chrome, firefox, opera, opera gx, brave, edge, vlc, vscode,
  telegram, obs, 7zip, git, ffmpeg. Other apps need an exact package ID after checking the publisher.
  Note: **Opera Mini is a phone-only app**; on a computer, Omni installs Opera or Opera GX.
- **Crypto wallets and extensions (Phantom, Rabby, Frontrun Pro)**: Omni opens only their official Chrome Web
  Store pages, with links taken from phantom.com, rabby.io and frontrun.pro, in a Chrome-based browser (Chrome,
  Brave or Edge), and you click "Add to Chrome" yourself. It never searches for wallet downloads, because fake
  "official" wallet sites are everywhere and they steal funds. None of the three works on Firefox (Phantom
  dropped Firefox support in 2025), and Omni tells you so instead of installing an outdated copy.
- **Firefox setup**: `setup_firefox` sets homepage, tracking protection, HTTPS-only mode, telemetry and more.

**What it won't do, on purpose:** create a crypto wallet for you, or save or send a recovery phrase or private key.
Anything the AI sees also goes to the AI company's servers and its logs, and seed phrases saved in files are
exactly what crypto-stealing malware searches for. Omni opens the official wallet page; you create the wallet and
write the phrase on paper. Omni can help you remove wallets afterwards.

**Safety rules built into the code** (the AI cannot turn them off, even with `-y`):
- It refuses to write a recovery phrase or private key into any file, or send a file containing one.
- It refuses to type anything that looks like a recovery phrase or private key.
- It never types passwords; you type those into the browser window yourself.
- It won't operate wallet or extension pages, create or import wallets, or approve transactions.
- Buttons like pay, buy, send, sign, approve, confirm and delete always ask you first.
- Screen recording and screenshots always ask you first.
- Telegram delivery only goes to *your* chat ID from `.env`, never anyone else.

Try it with no API key: `python examples/demo_desktop.py`

Other things it handles: drawing pictures (it writes a small drawing program and hands you the image) and web
searches in a visible browser. Google often shows automated browsers a CAPTCHA ("unusual traffic"); Omni doesn't
try to defeat those, and switches to another search engine or reads sources directly.

Limits: on Linux with Wayland (the default on many new distros), screen capture may fail; log in with an
"Xorg" session instead. Installing on Linux without Flatpak needs your admin password, so Omni gives
you the exact command to run. Websites with CAPTCHAs or bot detection may block the automated browser.

## Quick start

**Needs:** Python 3.9 or newer, and one model API key (or Ollama for free local models).

### Windows
1. Install Python from https://python.org (tick **"Add python.exe to PATH"**).
2. Unzip this folder, double-click **install.bat**.
3. Open **.env** in Notepad, paste your key after `ANTHROPIC_API_KEY=` and save.
4. Double-click **run.bat**, then type a goal.

### Linux / macOS
```bash
unzip omni-agent.zip && cd omni-agent
./install.sh
nano .env            # paste your API key
./run.sh             # interactive
./run.sh "research the best free tools for learning calculus and make me a 4-week plan"
./run.sh -f examples/cancer_research.txt
```

### Getting a key
| Provider | Where | Set in .env |
|---|---|---|
| Anthropic (Claude) | https://console.anthropic.com | `OMNI_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...` |
| OpenAI | https://platform.openai.com/api-keys | `OMNI_PROVIDER=openai`, `OPENAI_API_KEY=...` |
| xAI (Grok) | https://console.x.ai | `OMNI_PROVIDER=grok`, `XAI_API_KEY=...` (default model grok-4.3; try `-m grok-4.7` for the newest) |
| Kimi (Moonshot) | https://platform.moonshot.ai | `OMNI_PROVIDER=kimi`, `MOONSHOT_API_KEY=...` (default `kimi-k2.6`; `-m kimi-k3` for the flagship) |
| OpenRouter (many models, one key) | https://openrouter.ai/keys | `OMNI_PROVIDER=openrouter`, `OPENROUTER_API_KEY=...` |
| Ollama (free, runs on your PC) | https://ollama.com, then `ollama pull qwen2.5:14b` | `OMNI_PROVIDER=ollama` |

API usage costs money per token; long research runs can cost a few dollars. Set a spending
limit in your provider's dashboard. Local models are free but weaker at long multi-step tasks.

## Options
```
-p, --provider   anthropic | openai | grok | openrouter | ollama
-m, --model      model name, e.g. claude-opus-5-5
-s, --max-steps  how many actions before it must stop (default 40)
-w, --workspace  folder it works in (default ./workspace)
-y, --yes        don't ask before running commands (only if you trust the task)
-f, --file       read a long goal from a text file
--no-dashboard   don't open the live Mission Control page
--port           dashboard port (default 8765)
```

## How it works

```
 your goal
    |
    v
 +----------------------------------------------+
 |  AGENT LOOP (omni/agent.py)                  |
 |                                              |
 |  model thinks --> picks tools --> tools run  |
 |       ^                              |       |
 |       +------ results fed back <-----+       |
 |                                              |
 |  every 8 steps: forced self-critique         |
 |  history too long: older steps summarized    |
 +----------------------------------------------+
    |            |               |
    v            v               v
 sub-agents   long-term      workspace/
 (delegate)   memory.json    (files, code, reports, logs)
```

1. **The loop.** The model receives your goal plus a menu of tools. It answers with tool calls
   ("search this", "run this code"). The program runs them and sends back the results. This
   repeats until the model calls `final_answer`. That loop is what turns a chatbot into an agent.
2. **The system prompt** (top of `agent.py`) teaches a working method: plan first, brainstorm
   several approaches including an unconventional one, test ideas with real code and real sources,
   try to break its own conclusions, and be honest about what is verified vs. speculative.
3. **Tools** (`tools.py`): `think`, `update_plan`, `run_shell`, `run_python`, `read_file`,
   `write_file`, `list_dir`, `web_search`, `fetch_url`, `remember`, `recall`, `delegate`,
   `record_claim`, `retract_claim`, `convene_council`, `browser`, `screen`, `launch_app`, `install_app`,
   `send_file`, `final_answer`.
4. **Checkpoints.** Every few steps it is told to step back and ask whether its approach is working.
   This counters the biggest weakness of agents: confidently digging in the wrong direction.
5. **Sub-agents.** `delegate` spins up a fresh agent with a clean context for an independent
   chunk of work, so the main agent stays focused.
6. **Context compaction.** When the conversation gets too long, older steps are summarized
   (keeping findings, numbers, URLs) so runs can go on for a long time.
7. **Memory.** `workspace/.omni/memory.json` persists between runs; the next run sees it.
8. **Desktop powers** (`desktop.py`): browser control, screen capture, app launch/install, wallet
   safety and Telegram delivery.
   **Evidence Ledger** (`evidence.py`), **playbook** (`playbook.py`), **dashboard** (`dashboard.py`,
   `dashboard.html`) and **live state** (`state.py`) implement the unique features above.
9. **Providers** (`providers.py`) translate one internal message format into the Anthropic or
   OpenAI-style API, so you can swap models with one setting.

## Limits of the Evidence Ledger (read this)
It checks that a quote really exists in the cited source. It cannot check that the source itself is right,
that the quote supports the claim the agent attached to it, or facts the agent never registered. Pages that
build their text with JavaScript may come back "unreachable". It is a strong filter against invented quotes
and numbers, not a guarantee of truth.

## Safety
- It asks before running shell commands, Python code or overwriting files (answer `a` to allow
  all for the session). `-y` skips asking; use it only for tasks you trust.
- File tools are locked to the `workspace/` folder unless you set `OMNI_ALLOW_OUTSIDE_WORKSPACE=1`.
- Shell commands are *not* sandboxed: a command can still touch anything your user account can.
  For risky experiments run it inside a VM, WSL, or Docker.
- Full transcripts of every run are saved in `workspace/.omni/logs/` so you can audit what it did.

## About "curing cancer"
Try `examples/cancer_research.txt`. The agent can do genuinely useful research work: synthesize
literature, map drug candidates and resistance mechanisms, generate cross-disciplinary hypotheses,
and design cheap experiments to test them. It cannot run wet-lab experiments or clinical trials,
and a language model can be confidently wrong, so treat outputs as starting points for experts,
never as medical advice.

## Extending it
Add a tool = add a schema to `SPECS` in `tools.py` and a method named `t_<toolname>`.
Ideas: a PubMed search tool, a browser via Playwright, image generation, email sending,
a vector database for bigger memory, or a web UI.
"# Omini-AI-AGENT" 
