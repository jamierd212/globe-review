"""Would switching the scorer change the picture?

    python3 -m score.compare                 about 300 articles, Gemini vs Claude
    python3 -m score.compare --n 150

Takes articles Claude has already scored, scores them again with the other
model - in groups by story, exactly as the hourly job would - and compares.
Nothing in the database is touched; the new answers go to data/compare/.

This is agreement between two models, not accuracy. There is no hand-marked
set yet, so neither model is known to be right; what this can show is whether
swapping one for the other would change what the globe says. Three questions,
in order of how much they matter:

  1. Do the two give the same answers item by item? (weighted kappa)
  2. Do they agree on the DIRECTION of each item - hostile, neutral, warm?
  3. Do they rank the papers the same way? This is the one that matters most
     for the globe: a scorer that is noisier item by item but puts the
     Guardian and the Mail in the same places is a scorer the picture survives.
"""

import json
import math
import os
import random
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import db                          # noqa: E402
from score import agreement, llm as L          # noqa: E402
from score import run as R, prompt as P        # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "compare")


def sample(con, n=300, seed=1, against=L.ANTHROPIC_MODEL):
    """Whole stories, drawn at random, until there are about n articles.
    Whole stories because that is how the hourly job groups them - comparing
    on scattered single articles would test a situation that never occurs."""
    ver = P.rubric_version()
    rows = [dict(r) for r in con.execute(
        "SELECT a.id, a.title, a.standfirst, a.url_canon, a.outlet_id, "
        "       s.id AS story_id, s.issue_id, s.target, s.name AS story, "
        "       sc.stance AS before "
        "FROM article_score sc "
        "JOIN article a ON a.id = sc.article_id "
        "JOIN story s ON s.id = sc.story_id "
        "WHERE sc.model = ? AND sc.rubric_version = ? "
        "AND s.target IS NOT NULL AND s.target != '' "
        "AND sc.issue_id IS s.issue_id", (against, ver))]
    by_story = {}
    for r in rows:
        by_story.setdefault(r["story_id"], []).append(r)
    order = sorted(by_story)
    random.Random(seed).shuffle(order)
    picked = []
    for sid in order:
        if len(picked) >= n:
            break
        picked += by_story[sid]
    return picked


def items_as_of(saved, cutoff):
    """The articles of an earlier comparison, with their stories and targets
    exactly as they stood at `cutoff` - rebuilt from the archive, which
    records when every change was made. Lets a new model be tested on the
    same questions an old one was asked, even after the stories have moved on.

    saved: a data/compare/*.jsonl file. Each item carries the earlier answers
    as `before` (the first model's) and `earlier_other`."""
    from ingest import archive
    old = {json.loads(l)["article_id"]: json.loads(l) for l in open(saved, encoding="utf-8")}
    stories, members, arts = {}, {}, {}
    for month in sorted(os.listdir(archive.SHARDS)):
        d = os.path.join(archive.SHARDS, month)
        if not os.path.isdir(d):
            continue
        for line in archive._read(os.path.join(d, "articles.jsonl")):
            if line["id"] in old:
                arts[line["id"]] = line
        for line in archive._read(os.path.join(d, "stories.jsonl")):
            if (line.get("exported_at") or "") <= cutoff:
                stories[line["id"]] = line
                for aid in line.get("members") or []:
                    members[aid] = line["id"]
        for line in archive._read(os.path.join(d, "members.jsonl")):
            if line["at"] <= cutoff:
                members[line["article_id"]] = line["story_id"]
    items = []
    for aid, o in old.items():
        st = stories.get(members.get(aid))
        a = arts.get(aid)
        if not st or not a or not (st.get("target") or "").strip():
            continue
        items.append({"id": aid, "title": a["title"], "standfirst": a["standfirst"],
                      "url_canon": a["url_canon"], "outlet_id": a["outlet_id"],
                      "story_id": st["id"], "issue_id": st["issue_id"],
                      "target": st["target"], "story": st["name"],
                      "before": o["claude"], "earlier_other": o["other"]})
    return items


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    return None if sxx == 0 or syy == 0 else sxy / math.sqrt(sxx * syy)


def by_paper(items, before, after):
    """Each paper's average stance under each model."""
    acc = {}
    for t in items:
        a, b = before.get(t["id"]), after.get(t["id"])
        if a is None or b is None:
            continue
        p = acc.setdefault(t["outlet_id"], [[], []])
        p[0].append(a); p[1].append(b)
    return {o: (sum(v[0]) / len(v[0]), sum(v[1]) / len(v[1]), len(v[0]))
            for o, v in acc.items()}


def verdict(r, corr):
    k, sign = r.get("kappa") or 0, r.get("same_sign") or 0
    if k >= 0.6 and sign >= 0.85 and (corr or 0) >= 0.8:
        return "CLOSE ENOUGH TO SWITCH: same answers, same direction, same papers."
    if k >= 0.4 and (corr or 0) >= 0.6:
        return ("USABLE, WITH CARE: the papers come out in similar places, but "
                "read the disagreements below before switching.")
    return "NOT GOOD ENOUGH: switching would change what the globe says."


def run(n=300, seed=1, db_path=None, provider="gemini"):
    R.load_env()
    os.environ["FPM_PROVIDER"] = provider
    con = db.connect(db_path)
    items = sample(con, n, seed)
    if not items:
        print("no Claude-scored articles to compare against")
        return 1
    model = L.model_name()
    print(f"{len(items)} articles from {len({t['story_id'] for t in items})} stories, "
          f"re-scored by {model}")

    answers = {}

    def keep(t, r, now):
        answers[t["id"]] = r
    R.score_groups(items, keep)

    def whole(v):
        # the rubric's scale is whole points; stored values are floats
        try:
            return None if v is None else int(round(float(v)))
        except (TypeError, ValueError):
            return None
    before = {t["id"]: whole(t["before"]) for t in items}
    after = {t["id"]: whole(answers[t["id"]].get("stance"))
             for t in items if t["id"] in answers}
    r = agreement.compare(before, after)
    papers = by_paper(items, before, after)
    corr = _pearson([v[0] for v in papers.values()], [v[1] for v in papers.values()])
    diffs = [after[i] - before[i] for i in after
             if after[i] is not None and before.get(i) is not None]
    bias = sum(diffs) / len(diffs) if diffs else 0.0

    os.makedirs(OUT, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(OUT, f"{model}-{stamp}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for t in items:
            f.write(json.dumps({"article_id": t["id"], "outlet": t["outlet_id"],
                                "story": t["story"], "title": t["title"],
                                "claude": t["before"], "other": after.get(t["id"]),
                                "reason": (answers.get(t["id"]) or {}).get("reason")},
                               ensure_ascii=False) + "\n")

    print("\n" + agreement.report(r))
    print(f"\naverage shift: {bias:+.2f} ({model} minus Claude; negative = harsher)")
    print("\neach paper's average stance      Claude   " + model[:14])
    for o, (a, b, k) in sorted(papers.items(), key=lambda kv: kv[1][0]):
        print(f"  {o:<14} {k:>4} articles     {a:+.2f}    {b:+.2f}")
    print(f"\npapers ranked the same way: r = "
          f"{'n/a' if corr is None else f'{corr:.2f}'}  (1.0 = identical order)")
    title = {t["id"]: t["title"] for t in items}
    worst = r.get("worst", [])[:8]
    if worst:
        print("\nbiggest disagreements (read these):")
        for w in worst:
            print(f"  Claude {w['a']:+.0f}  {model[:12]} {w['b']:+.0f}   {title[w['item']][:80]}")
    print("\n" + verdict(r, corr))
    print(f"answers saved to {os.path.relpath(path, ROOT)}")
    con.close()
    return 0


def run_saved(saved, cutoff, referee=None, provider="anthropic"):
    """Ask a model the same questions as an earlier comparison, then mark it,
    the first model and the earlier second model against the referee's key.
    Agreement with another model is a weak test - the first comparison found
    the old model itself wrong about one time in ten - so the referee's key
    is the headline."""
    R.load_env()
    os.environ["FPM_PROVIDER"] = provider
    items = items_as_of(saved, cutoff)
    model = L.model_name() if provider == "gemini" else L.ANTHROPIC_MODEL
    print(f"{len(items)} articles from {len({t['story_id'] for t in items})} stories "
          f"(targets as of {cutoff}), scored by {model}")
    answers = {}
    tok = {"in": 0, "out": 0}
    real = L.complete

    def counted(system, user, max_tokens=300):
        out, i, o = real(system, user, max_tokens)
        tok["in"] += i; tok["out"] += o
        return out, i, o
    L.complete = counted
    try:
        R.score_groups(items, lambda t, r, now: answers.__setitem__(t["id"], r))
    finally:
        L.complete = real

    def whole(v):
        try:
            return None if v is None else int(round(float(v)))
        except (TypeError, ValueError):
            return None
    new = {t["id"]: whole((answers.get(t["id"]) or {}).get("stance")) for t in items}
    old = {t["id"]: whole(t["before"]) for t in items}
    other = {t["id"]: whole(t["earlier_other"]) for t in items}

    print(f"\n{tok['in']:,} tokens in, {tok['out']:,} out")
    for name, ref in (("Haiku 4.5 (Sept)", old), ("Gemini Flash (Sept)", other)):
        r = agreement.compare(ref, new)
        print(f"  agreement with {name:<20} kappa {r['kappa']}  same sign {r['same_sign']}  "
              f"within 1 point {r['within_1']}")
    if referee:
        key = {int(k): v for k, v in json.load(open(referee))["direction"].items()}
        sign = lambda v: None if v is None else (v > 0) - (v < 0)
        print(f"\nagainst the referee's key ({len(key)} disputed headlines, direction only):")
        for name, d in (("Haiku 4.5 (Sept)", old), ("Gemini Flash (Sept)", other), (model, new)):
            marked = [(k, sign(d.get(k))) for k in key if d.get(k) is not None]
            right = sum(1 for k, s_ in marked if s_ == key[k])
            wrong = sum(1 for k, s_ in marked if s_ == -key[k])
            print(f"  {name:<22} right {right:>2}   backwards {wrong:>2}   "
                  f"neutral {len(marked) - right - wrong:>2}   no answer {len(key) - len(marked)}")
        title = {t["id"]: t["title"] for t in items}
        miss = [k for k in key if sign(new.get(k)) == -key[k]]
        if miss:
            print(f"\n{model} backwards on:")
            for k in miss:
                print(f"  {title.get(k, k)[:90]}   ({(answers.get(k) or {}).get('reason')})")
    papers = by_paper(items, other, new)
    corr = _pearson([v[0] for v in papers.values()], [v[1] for v in papers.values()])
    print(f"\npapers ranked the same way as Gemini Flash: r = "
          f"{'n/a' if corr is None else f'{corr:.2f}'}")
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, f"{model}-{datetime.now(timezone.utc):%Y-%m-%d}.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for t in items:
            f.write(json.dumps({"article_id": t["id"], "outlet": t["outlet_id"],
                                "story": t["story"], "target": t["target"], "title": t["title"],
                                "claude": t["before"], "other": new.get(t["id"]),
                                "flash": t["earlier_other"],
                                "reason": (answers.get(t["id"]) or {}).get("reason")},
                               ensure_ascii=False) + "\n")
    print(f"answers saved to {os.path.relpath(path, ROOT)}")
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    n = int(a[a.index("--n") + 1]) if "--n" in a else 300
    sys.exit(run(n=n))
