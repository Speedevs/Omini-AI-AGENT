"""
PLAY CHESS WITH OMNI WHILE IT WORKS

Open the dashboard (it opens with every run) and play in the "Play while I work" board. Omni answers each of
your moves in the background, in parallel with whatever task it's doing, and explains its move in one sentence.

Same principle as the rest of Omni: THE AI PROPOSES, CODE VERIFIES.
  - The AI picks a move and says why.
  - The python-chess rules engine (code, not AI) checks it's legal.
  - If the AI proposes an illegal move, that's caught and shown to you, the AI gets one retry, and if it fails
    again a simple code fallback plays instead, clearly labelled. Omni can't cheat and can't hide mistakes.
"""
import json
import random
import re
import threading

try:
    import chess
except ImportError:  # optional dependency
    chess = None

MOVE_PROMPT = """You are Omni, playing a friendly game of chess against the user while you work on a task.
You play {color}. Choose ONE move from the legal moves list. Think briefly like a club player: safety first,
then threats, then development/activity. Reply with ONLY JSON:
{{"move": "<one move exactly as written in the legal list>", "why": "<one short, friendly sentence>"}}"""


class ChessGame:
    def __init__(self, provider_fn, state):
        self.provider_fn = provider_fn   # returns the provider to use (lets the agent pick it at run time)
        self.state = state
        self.lock = threading.Lock()
        self.new()

    # -------------------------------------------------------------- public
    def available(self):
        return chess is not None

    def new(self, omni_color="black"):
        with (self.lock if hasattr(self, "board") else threading.Lock()):
            self.board = chess.Board() if chess else None
            self.omni_color = omni_color
            self.log = []          # [{"by": "you"|"omni", "san", "why", "note"}]
            self.thinking = False
            self.caught = 0        # illegal moves the AI proposed and code rejected
        self._publish()
        if chess and omni_color == "white":
            self._omni_turn_async()

    def user_move(self, move: str):
        if not chess:
            return {"ok": False, "error": "Chess needs the python-chess package: pip install chess"}
        with self.lock:
            if self.thinking:
                return {"ok": False, "error": "Omni is still thinking."}
            if self.board.is_game_over():
                return {"ok": False, "error": "The game is over. Start a new one."}
            if self._omni_to_move():
                return {"ok": False, "error": "It's Omni's turn."}
            mv = self._parse(move)
            if not mv:
                return {"ok": False, "error": f"'{move}' isn't a legal move here."}
            san = self.board.san(mv)
            self.board.push(mv)
            self.log.append({"by": "you", "san": san})
        self._publish()
        if not self.board.is_game_over():
            self._omni_turn_async()
        return {"ok": True}

    # -------------------------------------------------------------- internals
    def _omni_to_move(self):
        return (self.board.turn == chess.WHITE) == (self.omni_color == "white")

    def _parse(self, text):
        text = (text or "").strip()
        for attempt in (lambda: chess.Move.from_uci(text.lower()), lambda: self.board.parse_san(text)):
            try:
                mv = attempt()
                if mv in self.board.legal_moves:
                    return mv
                # allow e7e8 without promotion letter -> queen
                if len(text) == 4:
                    q = chess.Move.from_uci(text.lower() + "q")
                    if q in self.board.legal_moves:
                        return q
            except Exception:
                continue
        return None

    def _omni_turn_async(self):
        with self.lock:
            self.thinking = True
        self._publish()
        threading.Thread(target=self._omni_turn, daemon=True).start()

    def _ask_ai(self, legal_san, note=""):
        color = self.omni_color
        history = " ".join(e["san"] for e in self.log[-30:]) or "(start of game)"
        prompt = (f"Position (FEN): {self.board.fen()}\nMoves so far: {history}\n"
                  f"Legal moves: {', '.join(legal_san)}\n{note}")
        reply = self.provider_fn().chat(MOVE_PROMPT.format(color=color),
                                        [{"role": "user", "content": prompt}], [])["content"]
        m = re.search(r"\{.*\}", reply, re.S)
        data = json.loads(m.group(0)) if m else {}
        return str(data.get("move", "")).strip(), str(data.get("why", "")).strip()

    def _fallback(self):
        """Simple code move if the AI fails twice: checkmate > capture of the biggest piece > check > random."""
        values = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}
        best, score = None, -1
        for mv in self.board.legal_moves:
            s = random.random()
            self.board.push(mv)
            if self.board.is_checkmate():
                s += 1000
            elif self.board.is_check():
                s += 2
            self.board.pop()
            cap = self.board.piece_at(mv.to_square)
            if cap:
                s += 10 * values[cap.piece_type]
            if s > score:
                best, score = mv, s
        return best

    def _omni_turn(self):
        note, why, entry_note = "", "", ""
        mv = None
        try:
            legal_san = [self.board.san(m) for m in self.board.legal_moves]
            for attempt in range(2):
                proposal, why = self._ask_ai(legal_san, note)
                mv = self._parse(proposal)
                if mv:
                    break
                self.caught += 1
                entry_note = f"The AI first proposed '{proposal}', which isn't legal here; code rejected it."
                note = f"Your previous choice '{proposal}' is NOT legal. Pick exactly from the legal list."
        except Exception as e:
            entry_note = f"The AI couldn't answer ({type(e).__name__})."
        if not mv:
            mv = self._fallback()
            why = "Backup move chosen by simple code rules, because the AI didn't give a legal move."
        with self.lock:
            san = self.board.san(mv)
            self.board.push(mv)
            self.log.append({"by": "omni", "san": san, "why": why, "note": entry_note})
            self.thinking = False
        self._publish()

    def _status(self):
        b = self.board
        if b.is_checkmate():
            winner = "white" if b.turn == chess.BLACK else "black"
            return f"Checkmate: {'Omni' if winner == self.omni_color else 'you'} won."
        if b.is_stalemate():
            return "Stalemate: draw."
        if b.is_insufficient_material() or b.can_claim_draw():
            return "Draw."
        if self.thinking:
            return "Omni is thinking..."
        return ("Check! " if b.is_check() else "") + ("Omni's move." if self._omni_to_move() else "Your move.")

    def _publish(self):
        if not chess:
            self.state.set(chess={"available": False})
            return
        self.state.set(chess={
            "available": True, "fen": self.board.fen(), "omni_color": self.omni_color,
            "status": self._status(), "thinking": self.thinking, "log": self.log[-40:], "caught": self.caught,
            "legal": [m.uci() for m in self.board.legal_moves] if not self._omni_to_move() else [],
            "last": self.board.peek().uci() if self.board.move_stack else "",
            "over": self.board.is_game_over()})
