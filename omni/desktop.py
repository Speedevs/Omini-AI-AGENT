"""
DESKTOP POWERS: things that touch your real computer beyond the terminal.

  browser      control a visible Chrome/Chromium window (open, read, click, type, screenshot)
  screen       take a screenshot or record the screen for N seconds (saved as .png / .mp4)
  launch_app   open an app, file or URL without the agent getting stuck waiting for it
  install_app  install apps from a vetted catalog using the OS package manager
  send_file    deliver a file to YOU on Telegram (your own chat only; optional)

SAFETY RULES BUILT INTO THE CODE (the AI cannot switch these off):
  - Crypto wallets and crypto extensions (Phantom, Rabby, Frontrun) are only ever opened from hardcoded
    official store links, never from search results. Extension files (.crx/.xpi) from other sites are blocked.
    Fake "official" wallet pages are one of the most common ways people lose their crypto.
  - The browser refuses to type anything that looks like a recovery phrase or private key.
  - The browser never types into password fields; you type passwords yourself.
  - The browser won't operate wallet/extension pages (chrome-extension://, moz-extension://).
  - Clicking buttons like pay, buy, send, sign, approve, confirm, delete always asks you first, even with -y.
  - Screen recording always asks you first, even with -y, because it can capture private information.
"""
import json
import os
import platform
import re
import shutil
import subprocess
import time
from pathlib import Path

import requests

from . import config
from .ui import ask_permission

SYSTEM = platform.system()  # "Windows", "Linux", "Darwin"

# ---------------------------------------------------------------- secret guard
_SEED_WORD = re.compile(r"^[a-z]{3,8}$")


def looks_secret(text: str) -> bool:
    """Recovery phrases (12-24 short lowercase words), hex private keys, long base58 keys."""
    t = text.strip()
    words = t.split()
    if len(words) in (12, 15, 18, 21, 24) and all(_SEED_WORD.match(w) for w in words):
        return True
    if re.fullmatch(r"(0x)?[0-9a-fA-F]{64}", t):
        return True
    if re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{80,90}", t):  # e.g. Solana secret key in base58
        return True
    return False


def contains_secret(text: str) -> bool:
    """True if any line, or any run of 12-24 words, looks like a recovery phrase or private key."""
    if looks_secret(text):
        return True
    for line in text.splitlines():
        if looks_secret(line):
            return True
    words = re.findall(r"[a-z]+", text.lower()) if len(text) < 200_000 else []
    for n in (12, 24):
        for i in range(0, max(0, len(words) - n + 1)):
            chunk = words[i:i + n]
            if all(3 <= len(w) <= 8 for w in chunk) and looks_secret(" ".join(chunk)) and \
                    len(set(chunk)) == n and not any(w in _COMMON for w in chunk):
                return True
    if re.search(r"(?<![0-9a-fA-F])0x[0-9a-fA-F]{64}(?![0-9a-fA-F])", text):
        return True  # 0x + 64 hex: the usual form of an Ethereum-style private key
    for m in re.finditer(r"(?<![0-9a-fA-F])[0-9a-fA-F]{64}(?![0-9a-fA-F])", text):
        near = text[max(0, m.start() - 60):m.start()].lower()
        if re.search(r"priv|secret|seed|key\b|mnemonic|wallet", near):
            return True  # bare 64 hex is often just a SHA-256 hash; only block it when labelled like a key
    return bool(
        re.search(r"(?<![1-9A-HJ-NP-Za-km-z])[1-9A-HJ-NP-Za-km-z]{85,90}(?![1-9A-HJ-NP-Za-km-z])", text))


# Everyday words that almost never appear in recovery phrases' random word lists together; used to avoid
# flagging ordinary sentences as seed phrases.
_COMMON = set("the and that with this from have they will your what when there their which would about".split())

SENSITIVE = re.compile(r"\b(pay|buy|purchase|checkout|order|send|transfer|withdraw|sign|approve|confirm|"
                       r"delete|remove|unsubscribe|submit|post|publish|place bid|swap|connect wallet)\b", re.I)

# ---------------------------------------------------------------- catalog
# Package IDs for each OS package manager. winget = Windows, brew = macOS, flatpak/snap = Linux.
CATALOG = {
    "chrome": {"winget": "Google.Chrome", "brew": "google-chrome", "flatpak": "com.google.Chrome"},
    "firefox": {"winget": "Mozilla.Firefox", "brew": "firefox", "flatpak": "org.mozilla.firefox", "snap": "firefox"},
    "opera": {"winget": "Opera.Opera", "brew": "opera", "flatpak": "com.opera.Opera", "snap": "opera"},
    "opera gx": {"winget": "Opera.OperaGX", "brew": "opera-gx"},
    "brave": {"winget": "Brave.Brave", "brew": "brave-browser", "flatpak": "com.brave.Browser", "snap": "brave"},
    "edge": {"winget": "Microsoft.Edge", "brew": "microsoft-edge", "flatpak": "com.microsoft.Edge"},
    "vlc": {"winget": "VideoLAN.VLC", "brew": "vlc", "flatpak": "org.videolan.VLC", "snap": "vlc"},
    "vscode": {"winget": "Microsoft.VisualStudioCode", "brew": "visual-studio-code", "flatpak": "com.visualstudio.code"},
    "telegram": {"winget": "Telegram.TelegramDesktop", "brew": "telegram", "flatpak": "org.telegram.desktop"},
    "obs": {"winget": "OBSProject.OBSStudio", "brew": "obs", "flatpak": "com.obsproject.Studio"},
    "7zip": {"winget": "7zip.7zip", "brew": "sevenzip"},
    "git": {"winget": "Git.Git", "brew": "git"},
    "ffmpeg": {"winget": "Gyan.FFmpeg", "brew": "ffmpeg"},
}
ALIASES = {"front run": "frontrun", "front run pro": "frontrun", "frontrun pro": "frontrun",
           "google chrome": "chrome", "mozilla": "firefox", "mozilla firefox": "firefox", "opera browser": "opera",
           "operagx": "opera gx", "brave browser": "brave", "microsoft edge": "edge", "vs code": "vscode",
           "visual studio code": "vscode", "obs studio": "obs", "7-zip": "7zip"}

# Browser extensions: ONLY these links, each copied from the developer's own official website (checked Sept 2026).
# Crypto-related extensions are where fake copies steal money, so Omni never takes these links from search results.
WALLETS = {
    "phantom": {"official": "https://phantom.com/download",
                "chrome": "https://chromewebstore.google.com/detail/phantom/bfnaelmomeimhlpmgjnjophhpkkoljpa",
                "firefox": None,
                "firefox_note": "Phantom no longer supports Firefox: its Firefox add-on stopped receiving updates "
                                "(last version May 2025). An unmaintained wallet is a security risk."},
    "rabby": {"official": "https://rabby.io/",
              "chrome": "https://chromewebstore.google.com/detail/rabby/acmacodkjbdgmoleebolmdjonilkdbch",
              "firefox": None, "firefox_note": "Rabby only publishes for Chrome-based browsers (Chrome, Brave, Edge)."},
    "frontrun": {"official": "https://frontrun.pro/", "kind": "extension",
                 "chrome": "https://chromewebstore.google.com/detail/frontrun/kifcalgkjaphbpbcgokommchjiimejah",
                 "firefox": None,
                 "firefox_note": "Frontrun Pro only publishes a Chrome extension (works in Chrome, Brave, Edge)."},
}
CHROMIUM_BROWSERS = ("chrome", "brave", "edge", "chromium")


def installed_chromium_browser(preferred=""):
    """Which Chrome-based browser is installed (preferring the one the user named). None if there isn't one."""
    home = Path.home()
    pf, pf86 = os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", str(home))
    where = {
        "chrome": {"Windows": [f"{pf}/Google/Chrome/Application/chrome.exe", f"{local}/Google/Chrome/Application/chrome.exe"],
                   "Darwin": ["/Applications/Google Chrome.app"], "Linux": ["google-chrome", "google-chrome-stable"]},
        "brave": {"Windows": [f"{pf}/BraveSoftware/Brave-Browser/Application/brave.exe",
                              f"{local}/BraveSoftware/Brave-Browser/Application/brave.exe"],
                  "Darwin": ["/Applications/Brave Browser.app"], "Linux": ["brave-browser", "brave"]},
        "edge": {"Windows": [f"{pf86}/Microsoft/Edge/Application/msedge.exe", f"{pf}/Microsoft/Edge/Application/msedge.exe"],
                 "Darwin": ["/Applications/Microsoft Edge.app"], "Linux": ["microsoft-edge", "microsoft-edge-stable"]},
        "chromium": {"Windows": [], "Darwin": ["/Applications/Chromium.app"], "Linux": ["chromium", "chromium-browser"]},
    }
    order = ([preferred] if preferred in where else []) + [b for b in where if b != preferred]
    for b in order:
        for c in where[b].get(SYSTEM, []):
            if (SYSTEM == "Linux" and shutil.which(c)) or (SYSTEM != "Linux" and Path(c).exists()):
                return b
    return None
EXTENSION_WORDS = ("extension", "add-on", "addon", "plugin")
WALLET_NOTE = ("Wallet safety: Omni opened the OFFICIAL page only. Click 'Add to Chrome' yourself. Omni will never "
               "create or import a wallet, never see or type your recovery phrase, and never approve transactions. "
               "Nobody legitimate will ever ask for your recovery phrase.")


def _run(cmd, timeout=900):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout + r.stderr)[-3000:]


# Games: official pages only (the web is full of look-alike "unblocked" copies).
GAMES = {"subway surfers": "https://poki.com/en/g/subway-surfers",   # SYBO's official web version, on Poki
         "subway surfers blast": "https://poki.com/en/g/subway-surfers-blast",
         "subway surfers match": "https://poki.com/en/g/subway-surfers-match"}

OFFICIAL_STORES = ("chromewebstore.google.com", "chrome.google.com/webstore", "addons.mozilla.org",
                   "microsoftedge.microsoft.com")


def unsafe_extension_url(url: str) -> bool:
    """Extension package files from anywhere but an official store are a classic malware route."""
    u = url.lower().split("?")[0]
    return u.endswith((".crx", ".xpi")) and not any(s in u for s in OFFICIAL_STORES)


def launch(target: str) -> str:
    """Start an app / open a file or URL, detached, so the agent does not wait for it to close."""
    if unsafe_extension_url(target):
        return "BLOCKED: extension files from unofficial sites are a common malware route. Use the official store."
    names = {"chrome": {"Windows": "chrome", "Darwin": "Google Chrome",
                        "Linux": ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]},
             "firefox": {"Windows": "firefox", "Darwin": "Firefox", "Linux": ["firefox"]},
             "opera": {"Windows": "opera", "Darwin": "Opera", "Linux": ["opera"]},
             "brave": {"Windows": "brave", "Darwin": "Brave Browser", "Linux": ["brave-browser", "brave"]},
             "edge": {"Windows": "msedge", "Darwin": "Microsoft Edge",
                      "Linux": ["microsoft-edge", "microsoft-edge-stable"]},
             "chromium": {"Windows": "chromium", "Darwin": "Chromium", "Linux": ["chromium", "chromium-browser"]}}
    first, _, rest = target.partition(" ")
    app = names.get(first.lower())
    kw = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    if SYSTEM == "Windows":
        if app:
            subprocess.Popen(f'start "" {app[SYSTEM]} {rest}', shell=True, **kw)
        else:
            subprocess.Popen(f'start "" "{target}"', shell=True, **kw)
    elif SYSTEM == "Darwin":
        subprocess.Popen(["open", "-a", app[SYSTEM]] + ([rest] if rest else []) if app else ["open", target], **kw)
    else:
        if app:
            exe = next((shutil.which(n) for n in app["Linux"] if shutil.which(n)), None)
            if not exe:
                return f"{first} is not installed. Try install_app('{first}')."
            subprocess.Popen([exe] + ([rest] if rest else []), start_new_session=True, **kw)
        elif re.match(r"https?://", target) or Path(target).exists():
            subprocess.Popen(["xdg-open", target], start_new_session=True, **kw)
        else:
            subprocess.Popen(target, shell=True, start_new_session=True, **kw)
    return f"Launched: {target}"


def _linux_admin():
    """How to run admin commands on Linux without typing a password: [] as root, sudo -n if allowed, else None."""
    if SYSTEM != "Linux":
        return None
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return []
    try:
        if subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0:
            return ["sudo", "-n"]
    except Exception:
        pass
    return None


MOZILLA_KEY_FPR = "35BAA0B33E9EB396F59CA838C0BA5CE6DC6315A3"  # Mozilla's published APT signing key


def _linux_firefox_via_mozilla_repo(admin):
    """Mozilla's official APT repository (the method on support.mozilla.org), with the key fingerprint checked."""
    script = f"""set -e
install -d -m 0755 /etc/apt/keyrings
wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg -O /etc/apt/keyrings/packages.mozilla.org.asc
FPR=$(gpg -n -q --import --import-options import-show /etc/apt/keyrings/packages.mozilla.org.asc | awk '/pub/{{getline; gsub(/^ +| +$/,""); print}}')
[ "$FPR" = "{MOZILLA_KEY_FPR}" ] || {{ echo "Mozilla key fingerprint mismatch: $FPR"; exit 1; }}
echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" > /etc/apt/sources.list.d/mozilla.list
printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' > /etc/apt/preferences.d/mozilla
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq firefox
firefox --version"""
    return admin + ["bash", "-c", script]


def install(name: str, workspace: Path, auto: bool, browser: str = "") -> str:
    key = ALIASES.get(name.lower().strip(), name.lower().strip())
    browser = (browser or "").lower()
    for w in WALLETS:  # "phantom on firefox" style requests
        if w in key.replace(" ", "") and "firefox" in key:
            browser = "firefox"
    for g in sorted(GAMES, key=len, reverse=True):
        if g in key or g.replace(" ", "") in key.replace(" ", ""):
            launch(GAMES[g])
            return (f"Opened {g.title()}'s official web version: {GAMES[g]} (made by SYBO, hosted by Poki). Nothing "
                    f"to install. Note: Omni can't play fast reaction games like this well (it needs seconds per "
                    f"move); the user plays it. Omni CAN play chess with them on the dashboard board.")
    if "chess" in key:
        return ("No install needed: the user can play chess against Omni on the dashboard's 'Play while I work' "
                "board, even while Omni is working on something else.")
    if "opera mini" in key:
        return ("Opera Mini is a phone app only (Android/iOS); there is no computer version. "
                "On a computer, install 'opera' (or 'opera gx') instead.")
    for w, links in WALLETS.items():
        if w in key.replace(" ", "") or w in key:
            if browser == "firefox":
                return (f"NOT INSTALLED on Firefox. {links['firefox_note']} Recommend the user adds {w.title()} to "
                        f"a Chrome-based browser (Chrome, Brave or Edge) instead: call install_app('{w}') without "
                        f"browser='firefox' to open its official Chrome Web Store page. Official site: {links['official']}")
            url = links["chrome"]
            kind = links.get("kind", "wallet")
            if not ask_permission(f"open the official {kind} page", f"{w.title()} {kind}\n{url}\n(from {links['official']})"):
                return "User declined."
            target = installed_chromium_browser(browser)
            if not target:
                return (f"No Chrome-based browser (Chrome, Brave, Edge) is installed, and {w.title()} needs one. "
                        f"Offer to install one first, e.g. install_app('brave') or install_app('chrome'), then retry.")
            launch(f"{target} {url}")  # never the default browser: it might be Firefox, which can't add these
            return (f"Opened {w.title()}'s official Chrome Web Store page in {target} (link taken from {links['official']}): {url}\n"
                    f"The user must click 'Add to Chrome' themselves; browsers require a person to approve extensions.\n"
                    f"Using Firefox or another browser? Use the official page instead: {links['official']}\n{WALLET_NOTE}")
    entry = CATALOG.get(key)
    if not entry and any(w in key for w in EXTENSION_WORDS):
        return (f"'{name}' is a browser extension that isn't in Omni's vetted list ({', '.join(WALLETS)}). "
                f"Find the developer's OFFICIAL website, and use only the store link published there "
                f"(chromewebstore.google.com or addons.mozilla.org). Never install .crx/.xpi files from other sites. "
                f"Open that store page with launch_app and let the user click install.")
    if key.split(":")[0] in ("winget", "brew", "flatpak", "snap"):  # explicit "winget:Some.Id" form
        entry = {key.partition(":")[0]: name.split(":", 1)[1].strip()}
    if not entry:
        return (f"'{name}' is not in Omni's vetted catalog. Find its exact package ID first "
                f"(Windows: run_shell 'winget search {name}', macOS: 'brew search {name}', Linux: "
                f"'flatpak search {name}'), check the publisher is the real developer, then call install_app "
                f"with e.g. 'winget:Publisher.App'. Catalog: {', '.join(sorted(CATALOG))}.")

    if SYSTEM == "Windows" and "winget" in entry:
        cmd = ["winget", "install", "--id", entry["winget"], "-e", "--accept-source-agreements",
               "--accept-package-agreements"]
    elif SYSTEM == "Darwin" and "brew" in entry and shutil.which("brew"):
        cmd = ["brew", "install", "--cask", entry["brew"]] if entry["brew"] not in ("git", "ffmpeg") \
            else ["brew", "install", entry["brew"]]
    elif SYSTEM == "Linux" and key == "firefox" and _linux_admin() is not None and shutil.which("apt-get"):
        cmd = _linux_firefox_via_mozilla_repo(_linux_admin())
    elif SYSTEM == "Linux" and "flatpak" in entry and shutil.which("flatpak"):
        _run(["flatpak", "remote-add", "--user", "--if-not-exists", "flathub",
              "https://dl.flathub.org/repo/flathub.flatpakrepo"])
        cmd = ["flatpak", "install", "--user", "-y", "flathub", entry["flatpak"]]  # --user: no admin password needed
    elif SYSTEM == "Linux" and "snap" in entry:
        return (f"Installing {name} on this Linux needs your admin password, which Omni can't type. "
                f"Run this yourself in a terminal:\n  sudo snap install {entry['snap']}\n"
                f"(or install Flatpak so Omni can install apps without a password: sudo apt install flatpak)")
    else:
        return f"No supported installer for {name} on {SYSTEM}. Install it from the developer's official website."

    if not (auto or ask_permission("install software", " ".join(cmd))):
        return "User declined."
    code, out = _run(cmd)
    return f"exit code {code}\n{out}"


def uninstall(name: str, auto: bool) -> str:
    key = ALIASES.get(name.lower().strip(), name.lower().strip())
    if any(w in key.replace(" ", "") for w in WALLETS) or any(w in key for w in EXTENSION_WORDS):
        return ("Browsers don't let programs remove extensions; a person clicks Remove. Use "
                "open_extensions_page for the browser concerned and tell the user which one to remove. "
                "For a wallet: make sure the user has their recovery phrase written down safely BEFORE removing it.")
    entry = CATALOG.get(key)
    if key.split(":")[0] in ("winget", "brew", "flatpak"):
        entry = {key.partition(":")[0]: name.split(":", 1)[1].strip()}
    if not entry:
        return f"'{name}' isn't in the catalog. Find its exact package ID (e.g. 'winget list {name}') first."
    admin = _linux_admin()
    if SYSTEM == "Windows" and "winget" in entry:
        cmd = ["winget", "uninstall", "--id", entry["winget"], "-e", "--accept-source-agreements"]
    elif SYSTEM == "Darwin" and "brew" in entry:
        cmd = ["brew", "uninstall", "--cask", entry["brew"]]
    elif SYSTEM == "Linux" and "flatpak" in entry and shutil.which("flatpak") and \
            _run(["flatpak", "info", "--user", entry["flatpak"]])[0] == 0:
        cmd = ["flatpak", "uninstall", "--user", "-y", entry["flatpak"]]
    elif SYSTEM == "Linux" and admin is not None and shutil.which("apt-get"):
        cmd = admin + ["apt-get", "remove", "-y", "-qq", key]
    else:
        return f"Removing {name} here needs your admin password. Run: sudo apt remove {key}  (or your package manager)"
    if not (auto or ask_permission("uninstall software", " ".join(cmd))):
        return "User declined."
    code, out = _run(cmd)
    return f"exit code {code}\n{out}"


def open_extensions_page(browser: str, names=()) -> str:
    """Help the user remove extensions. Chrome-based browsers refuse to let other programs open their internal
    chrome://extensions page, so for vetted extensions Omni opens the official store page instead: when the
    extension is installed, that page shows a 'Remove from Chrome' button."""
    b = (browser or "chrome").lower()
    if b == "firefox":
        launch("firefox about:addons")
        return "Opened Firefox's Add-ons page (about:addons). The user removes or disables extensions there."
    known = [n for n in names if n in WALLETS]
    for n in known:
        launch(f"{b} {WALLETS[n]['chrome']}")
    menu = {"edge": "... menu > Extensions > Manage extensions", "brave": "menu > Extensions > Manage extensions"}.get(
        b, "the three-dot menu > Extensions > Manage Extensions")
    msg = (f"Opened the official store page for {', '.join(known)} in {b}: click 'Remove from Chrome' there. "
           if known else "")
    return msg + (f"For any other extension: in {b}, open {menu} and click Remove. ({b} doesn't allow programs "
                  f"to open that page directly.)")


# Friendly Firefox settings -> Firefox preference names. Written to the profile's user.js, Firefox's own
# supported way to set preferences; applied the next time Firefox starts. Re-running replaces Omni's block only.
FIREFOX_PREFS = {
    "homepage": lambda v: {"browser.startup.homepage": v, "browser.startup.page": 1},
    "restore_session": lambda v: {"browser.startup.page": 3} if v else {},
    "strict_tracking_protection": lambda v: {"browser.contentblocking.category": "strict"} if v else {},
    "https_only": lambda v: {"dom.security.https_only_mode": bool(v)},
    "telemetry_off": lambda v: {"datareporting.healthreport.uploadEnabled": False,
                                "datareporting.policy.dataSubmissionEnabled": False,
                                "toolkit.telemetry.enabled": False} if v else {},
    "ask_where_to_save": lambda v: {"browser.download.useDownloadDir": not v},
    "dark_theme": lambda v: {"extensions.activeThemeID": "firefox-compact-dark@mozilla.org",
                             "layout.css.prefers-color-scheme.content-override": 0} if v else {},
    "block_popups": lambda v: {"dom.disable_open_during_load": bool(v)},
}


def _firefox_profile_dirs():
    home = Path.home()
    roots = [Path(os.environ.get("APPDATA", home)) / "Mozilla/Firefox/Profiles",
             home / ".mozilla/firefox", home / ".config/mozilla/firefox",  # newer Firefox on Linux uses ~/.config
             home / "snap/firefox/common/.mozilla/firefox",
             home / ".var/app/org.mozilla.firefox/.mozilla/firefox",
             home / "Library/Application Support/Firefox/Profiles"]
    found = []
    for r in roots:
        if r.is_dir():
            found += [d for d in r.iterdir() if d.is_dir() and ".default" in d.name]
    return found


def setup_firefox(settings: dict, before_change=None) -> str:
    profiles = _firefox_profile_dirs()
    if not profiles and shutil.which("firefox"):
        # A fresh install has no profile until Firefox runs once: start it invisibly for a few seconds.
        p = subprocess.Popen(["firefox", "--headless"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(8)
        p.terminate()
        time.sleep(2)
        profiles = _firefox_profile_dirs()
    if not profiles:
        return "No Firefox profile found. Is Firefox installed? Open it once, close it, and try again."
    prefs, unknown = {}, []
    for k, v in settings.items():
        if k in FIREFOX_PREFS:
            prefs.update(FIREFOX_PREFS[k](v))
        else:
            unknown.append(k)
    lines = ["// ---- Omni settings start (safe to delete this block) ----"]
    lines += [f'user_pref("{k}", {json.dumps(v)});' for k, v in prefs.items()]
    lines.append("// ---- Omni settings end ----")
    block = "\n".join(lines) + "\n"
    for prof in profiles:
        f = prof / "user.js"
        if before_change:
            before_change(f)
        old = f.read_text(encoding="utf-8") if f.exists() else ""
        old = re.sub(r"// ---- Omni settings start.*?// ---- Omni settings end ----\n?", "", old, flags=re.S)
        f.write_text(old + block, encoding="utf-8")
    running = any("firefox" in l.lower() for l in _run(["tasklist"] if SYSTEM == "Windows" else ["ps", "-eo", "comm"])[1].splitlines())
    return (f"Wrote {len(prefs)} Firefox settings to {len(profiles)} profile(s): {', '.join(sorted(prefs))}."
            + (f" Unknown options ignored: {', '.join(unknown)} (supported: {', '.join(FIREFOX_PREFS)})." if unknown else "")
            + (" Firefox is running: settings apply after it is closed and reopened." if running
               else " They apply the next time Firefox starts.")
            + " Default search engine can't be set this way; the user picks it in Settings > Search."
            + " On its very first launch Firefox shows a 'Welcome to Firefox' screen with its Terms of Use: the USER "
              "must click Continue themselves (never accept terms for them); the homepage shows after that.")


# ---------------------------------------------------------------- screen
def screenshot(path: Path) -> str:
    import mss
    import mss.tools
    with mss.mss() as sct:
        mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        img = sct.grab(mon)
        mss.tools.to_png(img.rgb, img.size, output=str(path))
    return f"Saved screenshot {img.size.width}x{img.size.height} to {path}"


def record(path: Path, seconds: float, fps: int = 10) -> str:
    """Record the main screen for `seconds` into an MP4. Real-time: if capture lags, frames are repeated."""
    import imageio_ffmpeg
    import mss
    seconds = max(0.5, min(float(seconds), 300))
    with mss.mss() as sct:
        mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        w, h = mon["width"], mon["height"]
        proc = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
                                 "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
                                 "-vf", f"crop={w - w % 2}:{h - h % 2}:0:0", "-c:v", "libx264", "-preset", "veryfast",
                                 "-pix_fmt", "yuv420p", str(path)], stdin=subprocess.PIPE)
        total, written, t0 = int(seconds * fps), 0, time.time()
        while written < total:
            frame = sct.grab(mon).raw
            due = min(total, int((time.time() - t0) * fps) + 1)
            for _ in range(max(1, due - written)):
                proc.stdin.write(frame)
                written += 1
            nxt = t0 + written / fps
            if nxt > time.time():
                time.sleep(nxt - time.time())
        proc.stdin.close()
        proc.wait()
    return f"Recorded {seconds:g}s of the screen ({w}x{h}, {fps} fps) to {path}"


# ---------------------------------------------------------------- telegram delivery
def send_telegram(path: Path, caption: str) -> str:
    token, chat = config.get("TELEGRAM_BOT_TOKEN"), config.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return ("Telegram isn't set up. The file is saved at the path above (and shown on the dashboard). "
                "To get files on your phone, set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (see README).")
    if path.stat().st_size > 49 * 1024 * 1024:
        return "File is over Telegram's 50 MB bot limit; it stays saved on your computer."
    method, field = ("sendVideo", "video") if path.suffix.lower() == ".mp4" else \
        ("sendPhoto", "photo") if path.suffix.lower() in (".png", ".jpg", ".jpeg") else ("sendDocument", "document")
    with path.open("rb") as f:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data={"chat_id": chat, "caption": caption[:1000]},
                          files={field: (path.name, f)}, timeout=120)
    ok = r.ok and r.json().get("ok")
    return "Sent to your Telegram." if ok else f"Telegram error: {r.text[:300]}"


def send_telegram_text(text: str) -> bool:
    token, chat = config.get("TELEGRAM_BOT_TOKEN"), config.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30,
                          data={"chat_id": chat, "text": text[:4000]})
        return bool(r.ok and r.json().get("ok"))
    except requests.RequestException:
        return False


# ---------------------------------------------------------------- browser
_TAG_JS = """() => {
  document.querySelectorAll('[data-omni-id]').forEach(e => e.removeAttribute('data-omni-id'));
  const els = [...document.querySelectorAll('a[href],button,input,textarea,select,[role=button],[role=link],[contenteditable=true]')]
    .filter(e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
                   return r.width > 2 && r.height > 2 && s.visibility !== 'hidden' && s.display !== 'none'; });
  return els.slice(0, 150).map((e, i) => { e.setAttribute('data-omni-id', i + 1);
    const label = (e.innerText || e.value || e.getAttribute('aria-label') || e.placeholder || e.title || e.name || '').trim();
    return [i + 1, e.tagName.toLowerCase() + (e.type ? ':' + e.type : ''), label.replace(/\\s+/g, ' ').slice(0, 80)]; });
}"""


_BROWSER = None


def get_browser(workspace: Path):
    """One shared browser window per run (sub-agents reuse it)."""
    global _BROWSER
    if _BROWSER is None:
        _BROWSER = Browser(workspace)
        import atexit
        atexit.register(_BROWSER.close)
    return _BROWSER


class Browser:
    """A real, visible browser window the agent drives (via Playwright). Uses installed Chrome if present."""

    def __init__(self, workspace: Path):
        self.ws = workspace
        self.pw = self.ctx = self.page = None

    def _start(self):
        if self.page:
            return
        from playwright.sync_api import sync_playwright
        self.pw = sync_playwright().start()
        profile = self.ws / ".omni" / "browser-profile"  # separate profile: your personal Chrome data is untouched
        headless = config.get("OMNI_BROWSER_HEADLESS") == "1"
        args = config.get("OMNI_BROWSER_ARGS", "").split()  # e.g. --start-maximized
        opts = dict(headless=headless, args=args, no_viewport=True)
        try:
            self.ctx = self.pw.chromium.launch_persistent_context(str(profile), channel="chrome", **opts)
        except Exception:
            self.ctx = self.pw.chromium.launch_persistent_context(str(profile), **opts)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()

    def _guard_url(self):
        if self.page and re.match(r"(chrome|moz)-extension://", self.page.url):
            raise PermissionError("Omni does not operate browser-extension pages (e.g. crypto wallets). Do it yourself.")

    def _elements(self):
        rows = self.page.evaluate(_TAG_JS)
        return "\n".join(f"[{i}] {kind} {label}" for i, kind, label in rows)

    def _view(self, note=""):
        self.page.wait_for_timeout(600)
        text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
        text = re.sub(r"\n\s*\n+", "\n", text)[:5000]
        return (f"{note}\nURL: {self.page.url}\nTITLE: {self.page.title()}\n\nPAGE TEXT:\n{text}\n\n"
                f"CLICKABLE / TYPEABLE ELEMENTS (use their numbers):\n{self._elements()}")

    def act(self, action, url=None, element=None, text=None, submit=False, path=None, auto=False):
        try:
            import playwright  # noqa: F401
        except ImportError:
            return "Browser control needs Playwright: run install again, or: pip install playwright && playwright install chromium"
        self._start()
        self._guard_url()
        if action == "open":
            if not re.match(r"https?://", url or ""):
                url = "https://" + (url or "")
            if unsafe_extension_url(url):
                return "BLOCKED: extension files from unofficial sites are a common malware route. Use the official store."
            self.page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return self._view()
        if action == "read":
            return self._view()
        if action == "back":
            self.page.go_back()
            return self._view()
        if action == "scroll":
            self.page.mouse.wheel(0, 900)
            return self._view()
        if action == "screenshot":
            self.page.screenshot(path=str(path))
            return f"Saved browser screenshot to {path}"
        if action == "close":
            self.close()
            return "Browser closed."
        loc = self.page.locator(f'[data-omni-id="{element}"]')
        if loc.count() == 0:
            return "That element number no longer exists; use action 'read' to get fresh numbers."
        label = (loc.inner_text(timeout=2000) if action == "click" else "") or loc.get_attribute("aria-label") or ""
        if action == "click":
            if SENSITIVE.search(label) and not ask_permission("click a sensitive button", f"'{label}' on {self.page.url}"):
                return "User declined this click."
            loc.click(timeout=10000)
            return self._view(f"Clicked [{element}] {label}")
        if action == "type":
            if looks_secret(text or ""):
                return ("BLOCKED: that looks like a recovery phrase or private key. Omni never types these. "
                        "Only the owner should ever enter them, into the official wallet app, by hand.")
            kind = (loc.get_attribute("type") or "").lower()
            if kind in ("checkbox", "radio", "button", "submit", "file", "image", "range", "color"):
                return f"Element [{element}] is a {kind}, not a text box. Pick a text/search box from the list."
            if kind == "password":
                return ("Omni does not type passwords. Ask the user to click the password box in the browser "
                        "window and type it themselves, then continue with 'read'.")
            loc.fill(text or "", timeout=10000)
            if submit:
                if not (auto or ask_permission("submit a form", f"{self.page.url}")):
                    return "Typed the text but the user declined submitting."
                loc.press("Enter")
            return self._view(f"Typed into [{element}]")
        return f"Unknown browser action '{action}'."

    def close(self):
        try:
            if self.ctx:
                self.ctx.close()
            if self.pw:
                self.pw.stop()
        finally:
            self.pw = self.ctx = self.page = None
