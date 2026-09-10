"""The gold set, end to end.

    python3 -m score.gold draw  --n 300                 pick what to label
    python3 -m score.gold label --who jamie             label it, one at a time
    python3 -m score.gold agree --a jamie --b sam       how much did you agree?
    python3 -m score.gold check --model runs/x.jsonl    how wrong is the model?

Label before you write the prompt. The point of doing it by hand is not the
labels — it is that you find out which parts of the rubric two people read
differently, and that is much cheaper to fix now than after a month of
scoring.

Two people should label the same items without discussing them. If you talk
first you will agree, and you will have learned nothing.
"""

import json
import os
import sys

from . import sample, agreement, calibrate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLD = os.path.join(ROOT, "data", "gold")
SCALE = {"-2": -2, "-1": -1, "0": 0, "1": 1, "2": 2,
         "--": -2, "-": -1, "+": 1, "++": 2}


def _path(name):
    os.makedirs(GOLD, exist_ok=True)
    return os.path.join(GOLD, name)


def _jsonl(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


# ------------------------------------------------------------------- draw ---

def cmd_draw(n=300, seed=1, db_path=None):
    sys.path.insert(0, ROOT)
    from ingest import db
    con = db.connect(db_path)
    rows = [dict(r) for r in con.execute(
        "SELECT a.id, a.outlet_id, a.title, a.standfirst, "
        "       s.issue_id, s.name AS story, s.target "
        "FROM article a "
        "LEFT JOIN story_member m ON m.article_id = a.id "
        "LEFT JOIN story s ON s.id = m.story_id")]
    if not rows:
        sys.exit("no articles yet - run `python3 -m ingest.run poll` first")

    picked = sample.stratified(rows, n=n, seed=seed)
    out = _path("sample.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for r in picked:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    cov = sample.coverage(picked)
    print(f"drew {cov['n']} items -> {out}")
    print("  per outlet: " + ", ".join(f"{k} {v}" for k, v in cov["outlets"].items()))
    if cov["too_thin_to_report"]:
        print("  too thin to report on: " + ", ".join(cov["too_thin_to_report"]))
    return 0


# ------------------------------------------------------------------ label ---

def cmd_label(who):
    items = _jsonl(_path("sample.jsonl"))
    if not items:
        sys.exit("no sample - run `draw` first")
    out = _path(f"labels-{who}.jsonl")
    done = {r["id"] for r in _jsonl(out)}
    todo = [i for i in items if i["id"] not in done]
    if not todo:
        print(f"all {len(items)} already labelled by {who}")
        return 0

    print(f"{len(todo)} left. -2 -1 0 1 2 to score, 'a' abstain, "
          f"'s' skip, 'q' save and stop.")
    print("Score how favourable it is TOWARDS THE TARGET shown. Not tone.\n")

    with open(out, "a", encoding="utf-8") as f:
        for n, it in enumerate(todo, 1):
            print("-" * 72)
            print(f"[{n}/{len(todo)}]  {it['title']}")
            if it.get("standfirst"):
                print(f"     {it['standfirst']}")
            print(f"  story:  {it.get('story') or '(not clustered yet)'}")
            print(f"  TARGET: {it.get('target') or '(none recorded)'}")
            try:
                raw = input("  score> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\nstopped."); break
            if raw == "q":
                print("saved."); break
            if raw == "s":
                continue
            if raw == "a":
                rec = {"id": it["id"], "score": None, "conf": None, "quote": None}
            elif raw in SCALE:
                conf = input("  confidence (h/m/l)> ").strip().lower() or "m"
                quote = input("  quote (blank = none)> ").strip()
                if not quote:
                    # the rubric's rule, enforced rather than trusted
                    print("  no quote -> recorded as 0, low confidence")
                    rec = {"id": it["id"], "score": 0, "conf": "l", "quote": None}
                else:
                    rec = {"id": it["id"], "score": SCALE[raw],
                           "conf": {"h": "high", "m": "medium", "l": "low"}.get(conf, "medium"),
                           "quote": quote}
            else:
                print("  didn't understand that, skipping")
                continue
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
    return 0


# ------------------------------------------------------- agree / calibrate ---

def _labels(who):
    return {r["id"]: r["score"] for r in _jsonl(_path(f"labels-{who}.jsonl"))}


def cmd_agree(a, b):
    la, lb = _labels(a), _labels(b)
    if not la or not lb:
        sys.exit(f"need labels from both {a} and {b}")
    r = agreement.compare(la, lb)
    print(agreement.report(r))
    if r["worst"]:
        items = {i["id"]: i for i in _jsonl(_path("sample.jsonl"))}
        print("\n  the headlines behind the worst disagreements:")
        for w in r["worst"][:8]:
            it = items.get(w["item"], {})
            print(f"    {w['a']:+d} vs {w['b']:+d}  {it.get('title','?')[:66]}")
    return 0


def cmd_check(model_path, who=None):
    model = {r["id"]: r.get("score") for r in _jsonl(model_path)}
    if who:
        gold = _labels(who)
    else:
        # average the humans where they both scored
        whos = [f[7:-6] for f in os.listdir(GOLD) if f.startswith("labels-")]
        per = [_labels(w) for w in whos]
        gold = {}
        for i in set().union(*[set(p) for p in per]) if per else set():
            vals = [p[i] for p in per if p.get(i) is not None]
            gold[i] = round(sum(vals) / len(vals)) if vals else None
    items = {i["id"]: i for i in _jsonl(_path("sample.jsonl"))}
    groups = {i: items[i]["outlet_id"] for i in items}
    print(calibrate.report(calibrate.calibrate(model, gold, groups)))
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default

    cmd = args[0]
    if cmd == "draw":
        sys.exit(cmd_draw(n=int(opt("--n", 300)), seed=int(opt("--seed", 1))))
    if cmd == "label":
        w = opt("--who")
        sys.exit(cmd_label(w) if w else "need --who <name>")
    if cmd == "agree":
        sys.exit(cmd_agree(opt("--a"), opt("--b")))
    if cmd == "check":
        sys.exit(cmd_check(opt("--model"), opt("--who")))
    sys.exit(__doc__)
