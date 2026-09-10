"""The direction regression.

The cheapest possible defence against the failure mode that matters most: a
stance target read the wrong way round, which does not blur a score but
inverts it. On a red-to-green globe an inverted issue is not slightly off, it
is the wrong colour, and it stays wrong until somebody notices by eye.

For every issue marked `regression: true` in the taxonomy, this asserts the
SIGN of (mean stance among left-leaning outlets) minus (mean stance among
right-leaning outlets). Not the magnitude - magnitudes move with the news and
asserting them would produce a test that cries wolf. Only the sign, which
should not move at all.

Run it on every scoring run. It costs nothing and it is the only thing
standing between a flipped constant and a month of archive scored backwards.
"""

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# how far from zero a mean has to be before we treat its sign as meaningful.
# below this, an issue is reported as INCONCLUSIVE rather than failed: a
# genuinely balanced week should not turn the build red.
DEADBAND = 0.15

# minimum items on each side before the comparison is made at all
MIN_ITEMS = 4


def load_taxonomy(path=None):
    with open(path or os.path.join(ROOT, "taxonomy.json"), encoding="utf-8") as f:
        return json.load(f)


def load_outlets(path=None):
    with open(path or os.path.join(ROOT, "outlets.json"), encoding="utf-8") as f:
        return json.load(f)


def expected_sign(expect):
    """left-minus-right, from the plain-words expectation.

    favourable > mixed > hostile. Only pairs that differ produce a sign; two
    'mixed' or two 'hostile' sides give 0, which means no assertion.
    """
    rank = {"hostile": -1, "mixed": 0, "favourable": 1}
    l = rank.get(expect.get("left"))
    r = rank.get(expect.get("right"))
    if l is None or r is None:
        return 0
    return (l > r) - (l < r)


def check(scores, taxonomy=None, outlets=None, deadband=DEADBAND, min_items=MIN_ITEMS):
    """scores: iterable of {issue_id, outlet_id, stance}

    Returns (ok, rows). `ok` is False only for a genuine sign inversion on an
    issue we said was not in doubt.
    """
    tax = taxonomy or load_taxonomy()
    outs = outlets or load_outlets()
    leaning = {o["id"]: o["leaning"] for o in outs["outlets"]}

    by_issue = {}
    for s in scores:
        by_issue.setdefault(s["issue_id"], []).append(s)

    rows, ok = [], True
    for issue in tax["issues"]:
        exp = issue.get("expect", {})
        if not exp.get("regression"):
            continue
        want = expected_sign(exp)
        items = by_issue.get(issue["id"], [])
        left = [s["stance"] for s in items if leaning.get(s["outlet_id"], 0) < -0.15]
        right = [s["stance"] for s in items if leaning.get(s["outlet_id"], 0) > 0.15]

        row = {"issue": issue["id"], "want": want,
               "n_left": len(left), "n_right": len(right)}

        if want == 0:
            row["verdict"] = "NO ASSERTION"
        elif len(left) < min_items or len(right) < min_items:
            row["verdict"] = "TOO FEW"
        else:
            ml, mr = sum(left) / len(left), sum(right) / len(right)
            diff = ml - mr
            row.update({"left_mean": round(ml, 2), "right_mean": round(mr, 2),
                        "diff": round(diff, 2)})
            if abs(diff) < deadband:
                row["verdict"] = "INCONCLUSIVE"
            elif (diff > 0) == (want > 0):
                row["verdict"] = "ok"
            else:
                row["verdict"] = "INVERTED"
                row["why"] = exp.get("why", "")
                ok = False
        rows.append(row)
    return ok, rows


def report(rows):
    out = [f"{'issue':<20} {'want':>5} {'left':>6} {'right':>6} {'diff':>6}  verdict"]
    for r in rows:
        out.append(
            f"{r['issue']:<20} {r['want']:>5} "
            f"{r.get('left_mean', ''):>6} {r.get('right_mean', ''):>6} "
            f"{r.get('diff', ''):>6}  {r['verdict']}"
            + (f"\n    -> {r['why']}" if r.get("why") else ""))
    bad = [r for r in rows if r["verdict"] == "INVERTED"]
    out.append("")
    out.append(f"{len(rows)} asserted, {len(bad)} INVERTED"
               + ("" if not bad else "  <-- check the stance target on these"))
    return "\n".join(out)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        sys.exit("usage: python3 -m score.direction <scores.json>\n"
                 "  scores.json: [{issue_id, outlet_id, stance}, ...]")
    with open(sys.argv[1], encoding="utf-8") as f:
        ok, rows = check(json.load(f))
    print(report(rows))
    sys.exit(0 if ok else 1)
