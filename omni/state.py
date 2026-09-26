"""Live mission state. Agents write events here; the dashboard reads it as JSON."""
import threading
import time


class MissionState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.data = {
            "goal": "", "model": "", "status": "idle", "step": 0, "started": time.time(),
            "plan": [], "feed": [], "claims": [], "agents": [], "council": [], "lessons": [], "files": [], "answer": "", "root": "", "chess": {},
        }

    def set(self, **kw):
        with self.lock:
            self.data.update(kw)

    def event(self, kind, text, depth=0):
        with self.lock:
            self.data["feed"].append({"t": time.strftime("%H:%M:%S"), "kind": kind, "text": text[:600], "depth": depth})
            self.data["feed"] = self.data["feed"][-250:]

    def add(self, key, item):
        with self.lock:
            self.data[key].append(item)
            return len(self.data[key]) - 1

    def update_item(self, key, index, **kw):
        with self.lock:
            self.data[key][index].update(kw)

    def snapshot(self):
        with self.lock:
            import copy
            snap = copy.deepcopy(self.data)
        snap["elapsed"] = int(time.time() - snap["started"])
        return snap


NULL_STATE = MissionState()  # used when no dashboard is running; harmless sink
