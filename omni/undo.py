"""
UNDO: every change Omni makes to your computer can be reversed.

While a mission runs, Omni writes an undo journal (workspace/.omni/undo.json). Each entry says what changed and
how to put it back:
  - a file Omni created            -> delete it
  - a file Omni overwrote          -> restore the saved copy of the original
  - Firefox settings Omni changed  -> restore the original user.js
  - an app Omni installed          -> uninstall it
  - a browser extension page       -> reopen the extensions page (browsers require YOU to click Remove)

See what can be undone:           python -m omni.undo --list
Undo the last mission:            python -m omni.undo
Undo a specific mission:          python -m omni.undo --mission 20260926_081500
Nothing is reversed without your confirmation.
"""
import json
import shutil
import sys
import time
from pathlib import Path


class Journal:
    def __init__(self, workspace: Path):
        self.ws = Path(workspace)
        self.path = self.ws / ".omni" / "undo.json"
        self.backups = self.ws / ".omni" / "undo_backups"
        self.mission = time.strftime("%Y%m%d_%H%M%S")

    def _load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")

    def record(self, what, undo):
        entries = self._load()
        entries.append({"mission": self.mission, "time": time.strftime("%H:%M:%S"), "what": what,
                        "undo": undo, "undone": False})
        self._save(entries)

    def before_file_change(self, path: Path, what):
        """Call BEFORE writing a file: remember how to restore or remove it."""
        path = Path(path)
        if path.exists():
            self.backups.mkdir(parents=True, exist_ok=True)
            copy = self.backups / f"{self.mission}_{int(time.time() * 1000)}_{path.name}"
            shutil.copy2(path, copy)
            self.record(what, {"type": "restore_file", "path": str(path), "backup": str(copy)})
        else:
            self.record(what, {"type": "delete_file", "path": str(path)})


def _reverse(entry, auto=False):
    from . import desktop
    u = entry["undo"]
    t = u["type"]
    if t == "delete_file":
        p = Path(u["path"])
        if p.exists():
            p.unlink()
        return f"deleted {p}"
    if t == "restore_file":
        shutil.copy2(u["backup"], u["path"])
        return f"restored the original {u['path']}"
    if t == "uninstall":
        return desktop.uninstall(u["name"], auto).splitlines()[0]
    if t == "extensions_page":
        return desktop.open_extensions_page(u["browser"], [u["name"]])
    return f"don't know how to undo '{t}'"


def main():
    args = sys.argv[1:]
    ws = Path(args[args.index("--workspace") + 1]) if "--workspace" in args else \
        Path(__file__).resolve().parent.parent / "workspace"
    j = Journal(ws)
    entries = j._load()
    live = [e for e in entries if not e["undone"]]
    if not live:
        print("Nothing to undo.")
        return
    missions = sorted({e["mission"] for e in live})
    if "--list" in args:
        for m in missions:
            print(f"\nMission {m}:")
            for e in live:
                if e["mission"] == m:
                    print(f"  [{e['time']}] {e['what']}")
        return
    target = args[args.index("--mission") + 1] if "--mission" in args else missions[-1]
    todo = [e for e in live if e["mission"] == target]
    print(f"Undo mission {target}? These changes will be reversed, newest first:")
    for e in reversed(todo):
        print(f"  - {e['what']}")
    if "--yes" not in args and input("Proceed? [y/N] ").strip().lower() != "y":
        print("Cancelled.")
        return
    pages = [e for e in todo if e["undo"]["type"] == "extensions_page"]
    for e in reversed(todo):
        try:
            if e["undo"]["type"] == "extensions_page":
                if e is pages[-1]:  # one visit to the extensions page covers all of them
                    names = ", ".join(x["undo"]["name"] for x in pages if x["undo"]["browser"] == e["undo"]["browser"])
                    from . import desktop
                    msg = desktop.open_extensions_page(e["undo"]["browser"], names.split(", "))
                    print(f"  {msg}")
                e["undone"] = True
                continue
            print(f"  {_reverse(e, auto=True)}")
            e["undone"] = True
        except Exception as ex:
            print(f"  FAILED: {e['what']} ({ex})")
    j._save(entries)
    print("Done.")


if __name__ == "__main__":
    main()
