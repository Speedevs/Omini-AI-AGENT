"""
TAMPER-EVIDENT RECEIPTS

Every mission produces a receipt folder:
  receipt.json   every step (AI reply, tool call, tool result) as a hash chain: each record includes the
                 SHA-256 hash of the one before it, so changing, removing or reordering anything breaks
                 every hash after it (the same idea a blockchain uses).
  snapshots/     the exact source text each claim was checked against, named by its SHA-256 hash, so the
                 evidence survives even if the web page later changes or disappears.
  proof.html     a readable page: every sentence of the answer colored by how well it's backed, and
                 clickable to its evidence.

Anyone can re-check a receipt, offline, without trusting Omni or any AI:
    python -m omni.verify path/to/receipt.json
"""
import hashlib
import html
import json
import time
from pathlib import Path

GENESIS = "0" * 64


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_hash(rec: dict) -> str:
    body = {k: rec[k] for k in ("i", "t", "kind", "data", "prev")}
    return sha(json.dumps(body, sort_keys=True, ensure_ascii=False))


class Receipt:
    def __init__(self, snapshot_dir: Path):
        self.chain = []
        self.snap_dir = snapshot_dir
        self.snap_dir.mkdir(parents=True, exist_ok=True)

    def add(self, kind, data):
        rec = {"i": len(self.chain), "t": time.strftime("%Y-%m-%dT%H:%M:%S"), "kind": kind, "data": data,
               "prev": self.chain[-1]["hash"] if self.chain else GENESIS}
        rec["hash"] = record_hash(rec)
        self.chain.append(rec)
        return rec["hash"]

    def snapshot(self, text: str) -> str:
        h = sha(text)
        p = self.snap_dir / f"{h}.txt"
        if not p.exists():
            p.write_text(text, encoding="utf-8")
        return h

    def export(self, folder: Path, goal, model, answer, claims, grounding):
        folder.mkdir(parents=True, exist_ok=True)
        snaps = folder / "snapshots"
        snaps.mkdir(exist_ok=True)
        for c in claims:
            if c.get("snapshot"):
                src = self.snap_dir / f"{c['snapshot']}.txt"
                if src.exists():
                    (snaps / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        final = self.add("final_answer", {"answer_sha256": sha(answer),
                                          "claims_sha256": sha(json.dumps(claims, sort_keys=True, ensure_ascii=False))})
        doc = {"format": "omni-receipt/1", "goal": goal, "model": model, "answer": answer, "claims": claims,
               "grounding": grounding, "chain": self.chain, "root": final}
        (folder / "receipt.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
        (folder / "proof.html").write_text(proof_html(doc), encoding="utf-8")
        return folder / "receipt.json", folder / "proof.html"


# ------------------------------------------------------------------ proof page
def _mark(passage, quote):
    """Escape the passage and highlight where the quote sits in it."""
    import re
    words = re.findall(r"\w+", quote)
    if len(words) >= 3:
        m1 = re.search(r"\W+".join(map(re.escape, words[:8])), passage, re.I)
        m2 = re.search(r"\W+".join(map(re.escape, words[-8:])), passage, re.I)
        if m1:
            end = m2.end() if m2 and m2.end() > m1.start() else m1.end()
            return (html.escape(passage[:m1.start()]) + "<mark>" + html.escape(passage[m1.start():end]) +
                    "</mark>" + html.escape(passage[end:]))
    return html.escape(passage)


def proof_html(doc):
    e = html.escape
    claims = doc["claims"]
    kinds = {"backed": "Backed by checked evidence", "speculative": "Labeled as speculative",
             "unbacked": "Not backed by evidence", "echo": "Repeats a rejected claim",
             "observed": "Seen in the mission's own tool output", "plain": "General statement"}
    counts = {k: sum(1 for g in doc["grounding"] if g["kind"] == k) for k in kinds}
    sent = []
    for g in doc["grounding"]:
        ref = f' data-c="{g["claim"]}"' if g.get("claim") else ""
        tip = f' title="{e(kinds[g["kind"]])}{" (claim #" + str(g["claim"]) + ")" if g.get("claim") else ""}"'
        sent.append(f'<span class="s {g["kind"]}"{ref}{tip} tabindex="0">{e(g["text"])}</span>')
    cards = []
    for i, c in enumerate(claims, 1):
        judge = f'<span class="tag j-{e(c.get("judge") or "none")}">Blind judge: {e(c.get("judge") or "not run")}</span>'
        corr = (f'<span class="tag">Other sites confirming: {c["corroborated"]} of {len(c.get("corroborating", []))}</span>'
                if c.get("corroborated") is not None else "")
        def other(o):
            if o.get("confirms"):
                verdict = "confirms"
            elif o["score"] >= 80:
                verdict = f"same keywords, but blind judge: {o.get('judge') or 'n/a'}"
            else:
                verdict = "doesn't discuss it together"
            why = f' <span class="meta">({e(o["judge_reason"])})</span>' if o.get("judge_reason") and not o.get("confirms") else ""
            return f'<li><a href="{e(o["url"])}">{e(o["domain"])}</a>: {e(verdict)}{why}</li>'
        others = "".join(other(o) for o in c.get("corroborating", []))
        cards.append(f'''<article class="claim" id="c{i}"><header><b>#{i}</b> {e(c["claim"])}</header>
<div class="tags"><span class="tag st-{e(c["status"].replace(" ", "-"))}">Quote check: {e(c["status"])}</span>{judge}{corr}</div>
<blockquote>{_mark(c.get("excerpt") or c["quote"], c["quote"])}</blockquote>
<p class="meta">Source: <a href="{e(c["source"])}">{e(c["source"])}</a>{"<br>Judge: " + e(c.get("judge_reason", "")) if c.get("judge_reason") else ""}
<br>Snapshot SHA-256: <code>{e(c.get("snapshot") or "none")}</code></p>{"<ul>" + others + "</ul>" if others else ""}</article>''')
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proof: {e(doc["goal"][:80])}</title><style>
:root{{--paper:#EEF1F4;--sheet:#fff;--ink:#1B2A41;--graphite:#5B6675;--rule:#D5DBE2;--ok:#0E7C66;--warn:#A86A12;--bad:#B42318;--spec:#5B4B8A}}
@media (prefers-color-scheme:dark){{:root{{--paper:#141A23;--sheet:#1C2430;--ink:#E4E9F0;--graphite:#9AA6B5;--rule:#2C3645;--ok:#3CC4A2;--warn:#E0A548;--bad:#F2776B;--spec:#A99BE0}}}}
body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.6 Georgia,"Times New Roman",serif}}
main{{max-width:760px;margin:0 auto;padding:40px 22px}}
h1{{font-size:28px;line-height:1.25;margin:0 0 6px}} h2{{font-size:20px;margin:36px 0 10px}}
.sub,.meta,.legend{{font:14px/1.5 system-ui,sans-serif;color:var(--graphite)}}
.answer{{background:var(--sheet);border:1px solid var(--rule);padding:22px 24px;font-size:18px;line-height:1.75}}
.s{{padding:1px 0;border-bottom:2px solid transparent;cursor:default}} .s[data-c]{{cursor:pointer}}
.s.backed{{border-color:var(--ok)}} .s.observed{{border-bottom:2px dotted var(--ok)}} .s.speculative{{border-bottom:2px dashed var(--spec)}}
.s.unbacked,.s.echo{{background:color-mix(in srgb,var(--bad) 14%,transparent);border-color:var(--bad)}}
.legend span{{margin-right:16px;white-space:nowrap}} .legend i{{display:inline-block;width:18px;height:3px;vertical-align:middle;margin-right:6px}}
.claim{{background:var(--sheet);border:1px solid var(--rule);padding:14px 18px;margin:12px 0}} .claim:target{{outline:3px solid var(--ok)}}
.tags{{margin:8px 0}} .tag{{display:inline-block;font:12px/1.4 system-ui,sans-serif;border:1px solid var(--rule);padding:2px 8px;margin:0 6px 6px 0}}
.st-verified,.j-supports{{color:var(--ok);border-color:var(--ok)}} .st-close-match,.j-partial{{color:var(--warn);border-color:var(--warn)}}
.st-not-found,.st-unreachable,.j-contradicts,.j-unrelated{{color:var(--bad);border-color:var(--bad)}} .st-retracted{{color:var(--graphite)}}
mark{{background:color-mix(in srgb,var(--ok) 22%,transparent);color:inherit;font-style:normal}}
blockquote{{margin:8px 0;padding-left:12px;border-left:3px solid var(--rule);font-style:italic;color:var(--graphite)}}
code{{font-size:12px;word-break:break-all}} a{{color:inherit}}
.seal{{margin-top:36px;padding:14px 18px;border:1px dashed var(--graphite);font:13px/1.6 system-ui,sans-serif}}
</style></head><body><main>
<p class="sub">Omni proof page, generated {e(doc["chain"][-1]["t"])} by {e(doc["model"])}</p>
<h1>{e(doc["goal"])}</h1>
<h2>Answer</h2>
<p class="legend"><span><i style="background:var(--ok)"></i>{kinds["backed"]} ({counts["backed"]})</span><span><i style="border-top:2px dotted var(--ok);height:0"></i>{kinds["observed"]} ({counts["observed"]})</span><span><i style="background:var(--spec)"></i>{kinds["speculative"]} ({counts["speculative"]})</span><span><i style="background:var(--bad)"></i>{kinds["unbacked"]} ({counts["unbacked"] + counts["echo"]})</span></p>
<div class="answer">{" ".join(sent)}</div>
<h2>Evidence</h2>{"".join(cards) or '<p class="sub">No claims were registered.</p>'}
<div class="seal">Tamper seal: this mission's {len(doc["chain"])} steps are hash-chained. Chain root:<br><code>{e(doc["root"])}</code><br>
Check it yourself, offline: <code>python -m omni.verify receipt.json</code></div>
</main><script>document.querySelectorAll('.s[data-c]').forEach(s=>s.addEventListener('click',()=>location.hash='c'+s.dataset.c))</script></body></html>'''
