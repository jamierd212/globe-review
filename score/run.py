"""Scoring what we have collected.

    python3 -m score.run tag              file each story under a subject
    python3 -m score.run score            score the articles in those stories
    python3 -m score.run score --batch     same thing at half price
    python3 -m score.run score --limit 40 --dry     estimate the cost first

Two model steps, in this order:

  tag    one call per story. Files it under one of the standing subjects, or
         none, and writes down what a favourable score would be favourable
         TOWARD. Sport and showbiz return none and drop out here, which is
         also what stops them reaching the globe.
  score  one call per article, against its story's target. This is the step
         that produces the colour.

Nothing but the headline and standfirst is ever sent. Not the body, even where
we have it - every paper has to be scored on the same instrument, and three of
the nationals give us nothing else.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import db                       # noqa: E402
from score import prompt as P               # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "claude-haiku-4-5-20251001"

# Haiku 4.5, dollars per million tokens. Batch is half.
PRICE_IN, PRICE_OUT = 1.00, 5.00


def load_env():
    """Read .env if the key is not already in the environment."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    path = os.path.join(ROOT, ".env")
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def client():
    if not load_env():
        sys.exit("no ANTHROPIC_API_KEY - put it in .env (which is gitignored):\n"
                 "  echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env")
    import anthropic
    return anthropic.Anthropic()


def parse_json(text):
    """Models occasionally wrap JSON in prose or a fence. Take the object."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n|\n```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
    return None


# Prompt caching is NOT on, and the marker has been removed rather than left
# in looking like it works.
#
# The rubric is identical on every call and is most of the bill, so caching it
# is the obvious saving. It silently did nothing. Measured directly by sending
# the same block at three sizes and watching the usage figures:
#
#     ~2.4k tokens   cache_write 0      cache_read 0        <- ignored
#     ~7k            cache_write 7,288  then read 7,288     <- works
#     ~14k           cache_write 14,575 then read 14,575    <- works
#
# So Haiku's minimum cacheable block is 4,096 tokens and this rubric is 2,439.
# The API accepts cache_control below that and ignores it - no error, no
# warning, and a bill three times the estimate. Padding the rubric to reach
# the threshold would work, but only if the extra 1,700 tokens are genuinely
# worth saying; filler to win a discount would be making the instrument worse
# to make it cheaper.
#
# The Batch API halves the cost instead, with no minimum and no effect on the
# prompt, and an hourly job does not care about latency.
def ask(cli, system, user, max_tokens=300, retries=4):
    blocks = [{"type": "text", "text": system}]
    for attempt in range(retries):
        try:
            r = cli.messages.create(
                model=MODEL, max_tokens=max_tokens, system=blocks,
                messages=[{"role": "user", "content": user}])
            txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            return parse_json(txt), r.usage.input_tokens, r.usage.output_tokens
        except Exception as e:
            if attempt == retries - 1:
                sys.stderr.write(f"  gave up: {type(e).__name__}: {e}\n")
                return None, 0, 0
            time.sleep(2 ** attempt)
    return None, 0, 0


def money(tin, tout):
    return tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT


# -------------------------------------------------------------------- tag ---

def cmd_tag(limit=None, dry=False, db_path=None):
    con = db.connect(db_path)
    issues = json.load(open(os.path.join(ROOT, "taxonomy.json"),
                            encoding="utf-8"))["issues"]
    # `expect` is a testing prior and must not reach the model
    safe = [{"id": i["id"], "name": i["name"], "target": i["target"]} for i in issues]

    todo = [dict(r) for r in con.execute(
        "SELECT id, name FROM story WHERE status != 'closed' "
        "AND (target IS NULL OR target = '') ORDER BY n_articles DESC")]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("every live story already has a target")
        return 0
    print(f"{len(todo)} stories to file")
    if dry:
        print(f"  would send about {len(todo)} calls")
        return 0

    cli = client()
    tin = tout = 0
    counts = {}
    for s in todo:
        heads = [r["title"] for r in con.execute(
            "SELECT a.title FROM article a JOIN story_member m ON m.article_id=a.id "
            "WHERE m.story_id=? LIMIT 8", (s["id"],))]
        out, i_, o_ = ask(cli, P.TAG_SYSTEM,
                          P.tag_prompt(s["name"], heads, safe), max_tokens=250)
        tin += i_; tout += o_
        if not out:
            continue
        iid = out.get("issue_id")
        target = (out.get("target") or "").strip()
        if iid and not target:
            target = next((i["target"] for i in safe if i["id"] == iid), "")
        con.execute("UPDATE story SET issue_id=?, target=?, target_conf=? WHERE id=?",
                    (iid, target, "inherited" if iid else "generated", s["id"]))
        counts[iid or "(none - not a news subject)"] = \
            counts.get(iid or "(none - not a news subject)", 0) + 1
    con.commit()
    print(f"\nfiled under:")
    for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:18]:
        print(f"  {v:>3}  {k}")
    print(f"\n{tin + tout} tokens, ${money(tin, tout):.3f}")
    con.close()
    return 0


# ------------------------------------------------------------------ score ---

def cmd_score(limit=None, dry=False, db_path=None, batch=False):
    con = db.connect(db_path)
    ver = P.rubric_version()
    todo = [dict(r) for r in con.execute(
        "SELECT a.id, a.title, a.standfirst, a.url_canon, s.id AS story_id, "
        "       s.issue_id, s.target, s.name AS story "
        "FROM article a "
        "JOIN story_member m ON m.article_id = a.id "
        "JOIN story s ON s.id = m.story_id "
        "LEFT JOIN article_score sc ON sc.article_id = a.id "
        "     AND sc.issue_id IS s.issue_id AND sc.rubric_version = ? "
        "WHERE s.target IS NOT NULL AND s.target != '' AND sc.article_id IS NULL "
        "ORDER BY s.n_articles DESC", (ver,))]
    if limit:
        todo = todo[:limit]
    if not todo:
        print("nothing to score - run `tag` first, or everything is already scored")
        return 0

    print(f"{len(todo)} articles to score against rubric {ver}")
    if dry:
        # measured from the real prompt, not guessed
        sysn = len(P.system_prompt()) // 4
        item = sum(len(P.item_prompt(t["title"], t["standfirst"], t["issue_id"] or "",
                                     t["target"])) for t in todo) // 4
        out = len(todo) * 90
        print(f"  ~{sysn * len(todo) + item:,} input tokens, ~{out:,} output")
        print(f"  ~${money(sysn * len(todo) + item, out):.2f} at list price, "
              f"~${money(sysn * len(todo) + item, out) / 2:.2f} batched")
        print("  (the system prompt is the bulk of it and is identical every "
              "call, so prompt caching would cut it hard)")
        return 0

    cli = client()
    system = P.system_prompt()
    if batch:
        return score_batched(cli, todo, system, ver, con)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tin = tout = 0
    kept = abstained = failed = 0
    for n, t in enumerate(todo, 1):
        desk = desk_from_url(t["url_canon"])
        out, i_, o_ = ask(cli, system,
                          P.item_prompt(t["title"], t["standfirst"],
                                        t["issue_id"] or t["story"], t["target"], desk))
        tin += i_; tout += o_
        if out is None:
            failed += 1
            continue
        stance = out.get("stance")
        if stance is None:
            abstained += 1
        else:
            kept += 1
        con.execute(
            "INSERT INTO article_score (article_id, issue_id, story_id, stance, "
            "tone, confidence, quote, desk, reason, rubric_version, model, scored_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(article_id, issue_id) "
            "DO UPDATE SET stance=excluded.stance, tone=excluded.tone, "
            "confidence=excluded.confidence, quote=excluded.quote, "
            "reason=excluded.reason, scored_at=excluded.scored_at",
            (t["id"], t["issue_id"], t["story_id"], stance, out.get("tone"),
             out.get("confidence"), out.get("quote"), desk, out.get("reason"),
             ver, MODEL, now))
        if n % 25 == 0:
            con.commit()
            print(f"  {n}/{len(todo)}  ${money(tin, tout):.2f}")
    con.commit()
    print(f"\n{kept} scored, {abstained} abstained, {failed} failed")
    print(f"{tin + tout:,} tokens, ${money(tin, tout):.2f} "
          f"(${money(tin, tout) / 2:.2f} if batched)")
    con.close()
    return 0


# ------------------------------------------------------------------ batch ---

def score_batched(cli, todo, system, ver, con, poll_every=20):
    """The same scoring, sent as one batch at half price.

    An hourly job does not care whether an answer comes back in two seconds or
    twenty minutes, and halving the bill is the difference between this costing
    $24 a month and $12.
    """
    import time as _t
    from datetime import datetime as _dt, timezone as _tz

    reqs = []
    for t in todo:
        reqs.append({
            "custom_id": f"a{t['id']}",
            "params": {
                "model": MODEL, "max_tokens": 300,
                "system": [{"type": "text", "text": system}],
                "messages": [{"role": "user", "content": P.item_prompt(
                    t["title"], t["standfirst"], t["issue_id"] or t["story"],
                    t["target"], desk_from_url(t["url_canon"]))}],
            },
        })

    print(f"sending {len(reqs)} in one batch")
    batch = cli.messages.batches.create(requests=reqs)
    print(f"  batch {batch.id}")
    while True:
        b = cli.messages.batches.retrieve(batch.id)
        c = b.request_counts
        if b.processing_status == "ended":
            break
        print(f"  {c.succeeded} done, {c.processing} running, {c.errored} errored")
        _t.sleep(poll_every)

    by_id = {f"a{t['id']}": t for t in todo}
    now = _dt.now(_tz.utc).isoformat(timespec="seconds")
    kept = abstained = failed = 0
    tin = tout = 0
    for res in cli.messages.batches.results(batch.id):
        t = by_id.get(res.custom_id)
        if t is None or res.result.type != "succeeded":
            failed += 1
            continue
        msg = res.result.message
        txt = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        out = parse_json(txt)
        tin += msg.usage.input_tokens
        tout += msg.usage.output_tokens
        if out is None:
            failed += 1
            continue
        stance = out.get("stance")
        kept += stance is not None
        abstained += stance is None
        con.execute(
            "INSERT INTO article_score (article_id, issue_id, story_id, stance, "
            "tone, confidence, quote, desk, reason, rubric_version, model, scored_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(article_id, issue_id) "
            "DO UPDATE SET stance=excluded.stance, tone=excluded.tone, "
            "confidence=excluded.confidence, quote=excluded.quote, "
            "reason=excluded.reason, scored_at=excluded.scored_at",
            (t["id"], t["issue_id"], t["story_id"], stance, out.get("tone"),
             out.get("confidence"), out.get("quote"),
             desk_from_url(t["url_canon"]), out.get("reason"), ver, MODEL, now))
    con.commit()
    print(f"\n{kept} scored, {abstained} abstained, {failed} failed")
    print(f"{tin + tout:,} tokens, ${money(tin, tout) / 2:.2f} at batch price")
    return 0


DESKS = [("comment", r"/(comment|opinion|columnists?|voices)/"),
         ("leader",  r"/(leader|editorial)s?/"),
         ("analysis", r"/(analysis|explainer|long-read)/"),
         ("feature", r"/(feature|magazine|lifestyle)s?/")]


def desk_from_url(url):
    """From the URL path, never inferred from the writing - inferring it from
    the tone would make desk and stance the same measurement."""
    for name, pat in DESKS:
        if re.search(pat, url or "", re.I):
            return name
    return "news"


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else ""
    lim = int(a[a.index("--limit") + 1]) if "--limit" in a else None
    dry = "--dry" in a
    if cmd == "tag":
        sys.exit(cmd_tag(limit=lim, dry=dry))
    if cmd == "score":
        sys.exit(cmd_score(limit=lim, dry=dry, batch="--batch" in a))
    sys.exit(__doc__)
