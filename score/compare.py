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


if __name__ == "__main__":
    a = sys.argv[1:]
    n = int(a[a.index("--n") + 1]) if "--n" in a else 300
    sys.exit(run(n=n))
