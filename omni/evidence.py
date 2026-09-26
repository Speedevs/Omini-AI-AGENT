"""
EVIDENCE LEDGER - Omni's anti-hallucination system.

Language models sometimes invent quotes, statistics and sources. Asking them to "be careful" isn't enough.
So instead of trusting the model, Omni checks mechanically:

  1. The agent registers a claim with a source (a URL or a workspace file) and the exact supporting quote.
  2. Omni itself fetches that source and searches it for the quote. The model is not involved in this check.
  3. The claim gets a stamp:
       verified      - the quote appears in the source (after normalizing spaces/punctuation/case)
       close match   - most of the quote's word sequences appear (e.g. slightly paraphrased)
       not found     - the source does not contain it (likely misremembered or fabricated)
       unreachable   - the source couldn't be loaded
       retracted     - the agent withdrew the claim after the check (shown, but not counted against trust)
  4. Every final answer ends with an audit: how many claims held up, and a trust score.
"""
import re

VERIFIED, CLOSE, NOT_FOUND, UNREACHABLE, RETRACTED = "verified", "close match", "not found", "unreachable", "retracted"
WEIGHT = {VERIFIED: 1.0, CLOSE: 0.5, NOT_FOUND: 0.0, UNREACHABLE: 0.0}


def normalize(text: str) -> str:
    text = text.lower().replace("\u2019", "'").replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"[^\w\s%.-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _shingles(words, n=3):
    return {" ".join(words[i:i + n]) for i in range(max(1, len(words) - n + 1))}


def check_quote(quote: str, source_text: str):
    """Return (status, match_percent)."""
    q, src = normalize(quote), normalize(source_text)
    if not q:
        return NOT_FOUND, 0
    if q in src:
        return VERIFIED, 100
    qs = _shingles(q.split())
    ss = _shingles(src.split())
    pct = int(100 * len(qs & ss) / len(qs)) if qs else 0
    if pct >= 70:
        return CLOSE, pct
    return NOT_FOUND, pct


def trust_score(claims):
    claims = [c for c in claims if c["status"] != RETRACTED]
    if not claims:
        return None
    return round(100 * sum(WEIGHT.get(c["status"], 0) for c in claims) / len(claims))


def audit_text(claims, observed=0):
    if not claims:
        if observed:
            return (f"\n\n--- Evidence audit ---\nNo outside sources were used. {observed} sentence(s) with "
                    f"numbers match output Omni's own tools produced during this mission (recorded in the receipt).")
        return ("\n\n--- Evidence audit ---\nNo claims were registered in the evidence ledger, "
                "so nothing above was independently checked. Treat factual statements with caution.")
    counts = {}
    for c in claims:
        counts[c["status"]] = counts.get(c["status"], 0) + 1
    lines = [f"\n\n--- Evidence audit (checked by Omni, not by the AI) ---",
             f"Trust score: {trust_score_v2(claims)}/100   " +
             ", ".join(f"{v} {k}" for k, v in counts.items())]
    for i, c in enumerate(claims, 1):
        extra = ""
        if c.get("judge"):
            extra += f" | blind judge: {c['judge']}"
        if c.get("corroborated") is not None:
            extra += f" | other sites confirming: {c['corroborated']} of {len(c.get('corroborating', []))}"
        lines.append(f"  #{i} [{c['status'].upper()}] {c['claim'][:140]}  ({c['source'][:90]}){extra}")
    if counts.get(RETRACTED):
        lines.append("  Retracted claims were caught by the ledger and withdrawn; they are not in the answer.")
    if counts.get(NOT_FOUND):
        lines.append("  Claims marked NOT FOUND were not supported by their cited source. Do not rely on them.")
    return "\n".join(lines)


# =====================================================================================
# PROOF-CARRYING ANSWERS: blind judge, independent corroboration, grounding gate
# =====================================================================================
from urllib.parse import urlparse

STOP = set("""the a an and or of to in on for with by from at as is are was were be been being this that these those
it its into than then which who whom whose what when where why how not no yes can could may might will would should
has have had do does did about above below over under up down out more most less least many much some any each
other such only own same so too very just also there their they them he she his her we our you your i me my""".split())

JUDGE_PROMPT = """You are a blind fact judge. You see ONE claim and ONE passage copied from its cited source.
You do not know who wrote the claim or why. Decide using ONLY the passage:
  supports    - the passage clearly states what the claim says (numbers, subjects and scope all match)
  partial     - the passage supports part of it, or the claim overstates/generalizes it
  contradicts - the passage says something incompatible (different number, subject or scope)
  unrelated   - the passage doesn't address the claim
Reply with ONLY JSON: {"verdict": "...", "reason": "<one short sentence>"}"""

JUDGE_WEIGHT = {"supports": 1.0, "partial": 0.6, "unrelated": 0.2, "contradicts": 0.0, None: 1.0}


def numbers_in(text):
    """Numbers that carry meaning (37, 37%, 2021, 0.73), not list markers."""
    return set(re.findall(r"(?<![\w.])\d+(?:[.,]\d+)?(?=%|\b)", text.replace(",", "")))


def key_terms(text):
    words = re.findall(r"[A-Za-z][A-Za-z0-9-]{3,}", text)
    terms = {w.lower() for w in words if w.lower() not in STOP and (len(w) >= 6 or w[0].isupper() or any(c.isdigit() for c in w))}
    return terms | numbers_in(text)


def _flex_pattern(quote, max_words=10):
    words = re.findall(r"\w+", quote)[:max_words]
    return re.compile(r"\W+".join(re.escape(w) for w in words), re.I) if len(words) >= 3 else None


def excerpt(quote, source_text, width=700):
    """The passage around the quote, cut by CODE from the real source text, in the source's own wording."""
    pat = _flex_pattern(quote)
    m = pat.search(source_text) if pat else None
    if m:
        tail = _flex_pattern(" ".join(re.findall(r"\w+", quote)[-10:]))
        m2 = tail.search(source_text, m.start()) if tail else None
        end = m2.end() if m2 and m2.end() - m.start() < len(quote) * 3 else m.end()
        a, b = max(0, m.start() - width // 2), min(len(source_text), end + width // 2)
        return re.sub(r"\s+", " ", source_text[a:b]).strip()
    src, q = normalize(source_text), normalize(quote)
    pos = src.find(q)
    if pos < 0:  # reworded quote: anchor where MOST of the quote's 3-word runs cluster in the source
        words = q.split()
        hits = []
        for i in range(max(0, len(words) - 2)):
            gram = " ".join(words[i:i + 3])
            start = src.find(gram)
            while start >= 0 and len(hits) < 400:
                hits.append(start)
                start = src.find(gram, start + 1)
        span = int(len(q) * 1.5) + 1
        best = max(hits, key=lambda h: sum(1 for x in hits if h <= x <= h + span), default=-1)
        pos = best
    return src[max(0, pos - width // 2):pos + len(q) + width // 2] if pos >= 0 else ""


def corroboration_score(claim, text, window=70):
    """Best share of the claim's key terms found TOGETHER within one ~70-word passage of another page.
    All of the claim's numbers must be in that same passage. Returns (score, passage). Only a pre-filter:
    passages that pass are then shown to the blind judge, which decides if they really support the claim."""
    terms = key_terms(claim)
    if not terms:
        return 0, ""
    words = normalize(text).split()
    nums = numbers_in(claim)
    best, passage = 0, ""
    for i in range(0, max(1, len(words) - window + 1), window // 3):
        chunk = " ".join(words[i:i + window])
        if nums and not all(re.search(rf"(?<![\d.]){re.escape(n)}(?![\d])", chunk) for n in nums):
            continue
        sc = int(100 * sum(1 for t in terms if t in chunk) / len(terms))
        if sc > best:
            best, passage = sc, chunk
        if best == 100:
            break
    return best, passage


def domain(url):
    d = urlparse(url).netloc.lower()
    return d[4:] if d.startswith("www.") else d


# ---------------- grounding gate ----------------
SPECULATIVE_MARKERS = re.compile(r"\b(speculative|hypothes[ie]s|toy model|toy simulation|estimate|unverified|"
                                 r"i (?:think|believe|suspect)|may|might|could|possibly|uncertain)\b", re.I)


def split_sentences(text):
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _coverage(sentence_terms, text):
    """Share of the sentence's key terms that appear in `text`'s key terms."""
    return len(sentence_terms & key_terms(text)) / len(sentence_terms) if sentence_terms else 0


_CODE_SPAN = re.compile(r"`[^`]*`")
_LIST_MARK = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")


def _checkable(sentence):
    """The part of a sentence that makes factual claims: no list markers, no `code`, no ``` fences."""
    if sentence.strip().startswith("```"):
        return ""
    return _CODE_SPAN.sub(" ", _LIST_MARK.sub("", sentence))


def observed_from_chain(chain):
    """Numbers that appeared in the mission's own tool outputs, recorded by code in the receipt."""
    out = set()
    for rec in chain:
        if rec.get("kind") == "tool":
            out.update(rec["data"].get("observed_numbers", []))
    return out


def ground(answer, claims, observed=None):
    """Classify every sentence of the answer. Pure code, no AI:
         backed       numbers all appear in one live claim, or (no numbers) nearly all key terms do
         echo         mostly restates a claim that was retracted or that the blind judge rejected
         speculative  contains unverified numbers but is clearly labeled as estimate/speculation
         unbacked     contains numbers no live claim supports
         observed     numbers all appeared in this mission's own tool outputs (commands, files, pages it read)
         plain        general prose with no checkable specifics"""
    observed = set(observed or ())
    live = [c for c in claims if c["status"] in (VERIFIED, CLOSE) and c.get("judge") not in ("contradicts", "unrelated")]
    dead = [c for c in claims if c["status"] == RETRACTED or c.get("judge") in ("contradicts", "unrelated")]
    num = {id(c): i + 1 for i, c in enumerate(claims)}
    out = []
    for s in split_sentences(answer):
        c_s = _checkable(s)
        nums, terms = numbers_in(c_s), key_terms(c_s)
        kind, backing = "plain", None
        echo = next((c for c in dead if len(terms) >= 3 and _coverage(terms, c["claim"]) >= 0.8), None)
        if echo:
            kind, backing = "echo", num[id(echo)]
        elif nums:
            hit = next((c for c in live if nums <= numbers_in(c["claim"] + " " + c["quote"])), None)
            if not hit:
                # A sentence may combine facts from several claims: every number must come from a live claim
                # about the same subject (sharing at least 2 key terms with the sentence).
                related = [c for c in live if len(terms & key_terms(c["claim"] + " " + c["quote"])) >= 2]
                covered = set().union(*(numbers_in(c["claim"] + " " + c["quote"]) for c in related)) if related else set()
                if related and nums <= covered:
                    hit = next(c for c in related if numbers_in(c["claim"] + " " + c["quote"]) & nums)
            if hit:
                kind, backing = "backed", num[id(hit)]
            elif nums <= observed:
                kind = "observed"
            else:
                kind = "speculative" if SPECULATIVE_MARKERS.search(s) else "unbacked"
        elif len(terms) >= 3:
            hit = next((c for c in live if _coverage(terms, c["claim"] + " " + c["quote"]) >= 0.8), None)
            if hit:
                kind, backing = "backed", num[id(hit)]
        out.append({"text": s, "kind": kind, "claim": backing})
    return out


def trust_score_v2(claims):
    live = [c for c in claims if c["status"] != RETRACTED]
    if not live:
        return None
    return round(100 * sum(WEIGHT.get(c["status"], 0) * JUDGE_WEIGHT.get(c.get("judge"), 1.0) for c in live) / len(live))
