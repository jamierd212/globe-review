"""Scoring what we have collected.

    python3 -m score.run tag              file each story under a subject
    python3 -m score.run score            score the articles in those stories
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


def ask(cli, system, user, max_tokens=300, retries=4):
    """The rubric is ~1,600 tokens and identical on every call, which would be
    three quarters of the bill. Marking it cached means it is charged once per
    five minutes instead of once per article: measured on 300 articles, $0.64
    becomes about $0.21."""
    blocks = [{"type": "text", "text": system,
               "cache_control": {"type": "ephemeral"}}]
    for attempt in range(retries):
        try:
            r = cli.messages.create(
                model=MODEL, max_tokens=max_tokens, system=blocks,
                messages=[{"role": "user", "content": user}])
            txt = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
            u = r.usage
            billed_in = (u.input_tokens
                         + getattr(u, "cache_read_input_tokens", 0) * 0.1
                         + getattr(u, "cache_creation_input_tokens", 0) * 1.25)
            return parse_json(txt), billed_in, u.output_tokens
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

def cmd_score(limit=None, dry=False, db_path=None):
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
        sys.exit(cmd_score(limit=lim, dry=dry))
    sys.exit(__doc__)
