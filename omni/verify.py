"""
Re-check an Omni receipt, offline, trusting nothing:   python -m omni.verify path/to/receipt.json

  1. Every step's hash is recomputed and every link to the previous step is checked (tampering breaks the chain).
  2. The final answer and the claims list must match the hashes sealed into the chain.
  3. Every evidence snapshot must match its SHA-256 name (no swapped sources).
  4. Every quote check is re-run against its snapshot and must give the same stamp.
  5. The sentence-by-sentence grounding of the answer is recomputed and must match.
  6. With --root, the chain must end at the root hash you kept elsewhere (e.g. the copy Omni sent to your
     Telegram). Without it, someone could edit the receipt AND rebuild every hash consistently.
"""
import json
import sys
from pathlib import Path

from .evidence import RETRACTED, check_quote, ground, observed_from_chain
from .receipts import GENESIS, record_hash, sha


def verify(path, root=None):
    path = Path(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    results = []

    def check(ok, what):
        results.append((bool(ok), what))

    prev, chain_ok = GENESIS, True
    for rec in doc["chain"]:
        if rec["prev"] != prev or record_hash(rec) != rec["hash"]:
            chain_ok = False
            check(False, f"Hash chain broken at step {rec['i']} ({rec['kind']})")
            break
        prev = rec["hash"]
    if chain_ok:
        check(prev == doc["root"], f"Hash chain intact: {len(doc['chain'])} steps, root {prev[:16]}...")
    if root:
        check(prev == root.strip(), "Root matches the copy you kept (so the whole chain wasn't rebuilt)")

    seal = doc["chain"][-1]["data"]
    check(seal.get("answer_sha256") == sha(doc["answer"]), "Final answer matches the sealed hash")
    check(seal.get("claims_sha256") == sha(json.dumps(doc["claims"], sort_keys=True, ensure_ascii=False)),
          "Evidence list matches the sealed hash")

    snaps = path.parent / "snapshots"
    for i, c in enumerate(doc["claims"], 1):
        if not c.get("snapshot"):
            continue
        f = snaps / f"{c['snapshot']}.txt"
        if not f.exists():
            check(False, f"Claim #{i}: snapshot file missing")
            continue
        text = f.read_text(encoding="utf-8")
        check(sha(text) == c["snapshot"], f"Claim #{i}: source snapshot is the original (SHA-256 matches)")
        expected = c.get("was") if c["status"] == RETRACTED else c["status"]
        got, _ = check_quote(c["quote"], text)
        check(got == expected, f"Claim #{i}: quote re-checked against snapshot -> {got} (recorded: {expected})")

    regrounded = [(g["kind"], g["claim"]) for g in ground(doc["answer"], doc["claims"], observed_from_chain(doc["chain"]))]
    check(regrounded == [(g["kind"], g["claim"]) for g in doc["grounding"]],
          "Sentence-by-sentence grounding recomputed and matches")
    return results


def main():
    args = sys.argv[1:]
    root = None
    if "--root" in args:
        i = args.index("--root")
        root = args[i + 1] if i + 1 < len(args) else None
        args = args[:i] + args[i + 2:]
    if len(args) != 1:
        print("usage: python -m omni.verify path/to/receipt.json [--root <hash you kept>]")
        sys.exit(2)
    results = verify(args[0], root)
    for ok, what in results:
        print(("  PASS  " if ok else "  FAIL  ") + what)
    bad = sum(1 for ok, _ in results if not ok)
    print(f"\n{'RECEIPT VALID' if not bad else f'RECEIPT INVALID: {bad} check(s) failed'}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
