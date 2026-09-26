"""MISSION CONTROL - a live dashboard at http://localhost:8765 (standard library only)."""
import json
import mimetypes
import threading
from urllib.parse import unquote
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .evidence import trust_score_v2 as trust_score

PAGE = Path(__file__).with_name("dashboard.html")


def start(state, port=8765, open_browser=True, workspace=None, game=None):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            """Chess moves from the board. Only this game is reachable this way; nothing else on your computer."""
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            except (ValueError, json.JSONDecodeError):
                body = {}
            if game and self.path == "/chess/move":
                out = game.user_move(str(body.get("move", "")))
            elif game and self.path == "/chess/new":
                game.new("white" if body.get("omni") == "white" else "black")
                out = {"ok": True}
            else:
                self.send_error(404)
                return
            data = json.dumps(out).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/files/") and workspace:
                rel = unquote(self.path[len("/files/"):].split("?")[0])
                # Only serve files the agent explicitly delivered, never arbitrary paths.
                if rel not in {f["path"] for f in state.snapshot()["files"]}:
                    self.send_error(404)
                    return
                f = (Path(workspace) / rel).resolve()
                if not f.is_file() or not f.is_relative_to(Path(workspace).resolve()):
                    self.send_error(404)
                    return
                body = f.read_bytes()
                ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            elif self.path.startswith("/state"):
                snap = state.snapshot()
                snap["trust"] = trust_score(snap["claims"])
                body = json.dumps(snap).encode("utf-8")
                ctype = "application/json"
            else:
                body = PAGE.read_bytes()
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):  # keep the terminal clean
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)  # localhost only: not visible to your network
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{port}"
    if open_browser:
        # In a thread: on some systems webbrowser.open() waits until the browser is closed.
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    return url
