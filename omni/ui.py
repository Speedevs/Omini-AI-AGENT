"""Terminal colors and permission prompts (works on Windows 10+ and Linux)."""
import os

if os.name == "nt":
    os.system("")  # turns on ANSI colors in the Windows console

_C = {"dim": "90", "cyan": "36", "green": "32", "yellow": "33", "red": "31", "bold": "1"}
_session_always = {"value": False}


def _c(code, text):
    return f"\033[{code}m{text}\033[0m"


def dim(t): return _c(_C["dim"], t)
def cyan(t): return _c(_C["cyan"], t)
def green(t): return _c(_C["green"], t)
def yellow(t): return _c(_C["yellow"], t)
def red(t): return _c(_C["red"], t)
def bold(t): return _c(_C["bold"], t)


def ask_permission(action, detail):
    """Ask before anything that touches your computer. 'a' = allow everything for the rest of this run."""
    if _session_always["value"]:
        return True
    print(yellow(f"\n  The agent wants to {action}:"))
    for line in detail.splitlines()[:40]:
        print(yellow("  | ") + line)
    ans = input(yellow("  Allow? [y]es / [n]o / [a]lways this session: ")).strip().lower()
    if ans == "a":
        _session_always["value"] = True
        return True
    return ans == "y"
