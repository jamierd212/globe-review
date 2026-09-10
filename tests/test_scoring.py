"""Gold-set tooling, tested against made-up labellers whose behaviour we
control. If the maths says two people who agree perfectly have poor agreement,
we would never find that out from real data."""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from score import sample, agreement, calibrate

def items(n=600, outlets=("mail","sky","i","guardian"), issues=("a","b","c")):
    r = random.Random(7)
    # deliberately lopsided: the Mail floods the sample, the i paper trickles
    weights = {"mail": 0.6, "sky": 0.25, "guardian": 0.12, "i": 0.03}
    out = []
    for k in range(n):
        o = r.choices(list(weights), weights=list(weights.values()))[0]
        out.append({"id": k, "outlet_id": o, "issue_id": r.choice(issues)})
    return out

# ---------------------------------------------------------------- sampling --
def test_sampling_does_not_let_one_outlet_dominate():
    s = sample.stratified(items(), n=200, per_outlet_min=20, seed=1)
    cov = sample.coverage(s)
    counts = list(cov["outlets"].values())
    assert max(counts) - min(counts) <= 30, cov["outlets"]
    assert cov["outlets"]["mail"] < 0.4 * cov["n"], "the Mail still dominates"

def test_sampling_is_repeatable():
    a = [i["id"] for i in sample.stratified(items(), 200, seed=3)]
    b = [i["id"] for i in sample.stratified(items(), 200, seed=3)]
    assert a == b, "two labellers would get different sets"

def test_thin_outlets_are_flagged_not_hidden():
    small = [{"id": i, "outlet_id": "i" if i < 5 else "mail", "issue_id": "a"}
             for i in range(200)]
    cov = sample.coverage(sample.stratified(small, 100, seed=1))
    assert "i" in cov["too_thin_to_report"], cov

# --------------------------------------------------------------- agreement --
def test_perfect_agreement_scores_one():
    a = {i: (i % 5) - 2 for i in range(100)}
    r = agreement.compare(a, dict(a))
    assert r["kappa"] == 1.0 and r["exact"] == 1.0

def test_both_saying_minus_one_to_everything_is_not_rewarded():
    """Raw agreement 100%, but neither has made a judgement."""
    a = {i: -1 for i in range(100)}
    r = agreement.compare(a, dict(a))
    assert r["exact"] == 1.0
    assert r["kappa"] in (0.0, 1.0)   # degenerate; the point is it is defined
    lazy_a = {i: -1 for i in range(100)}
    lazy_b = {i: -1 if i % 10 else 1 for i in range(100)}
    r2 = agreement.compare(lazy_a, lazy_b)
    assert r2["kappa"] <= 0.01, f"lazy labelling scored {r2['kappa']}"

def test_near_misses_hurt_less_than_opposites():
    ids = range(200)
    truth = {i: (i % 5) - 2 for i in ids}
    near = {i: max(-2, min(2, truth[i] + (1 if i % 2 else -1))) for i in ids}
    flip = {i: -truth[i] for i in ids}
    k_near = agreement.compare(truth, near)["kappa"]
    k_flip = agreement.compare(truth, flip)["kappa"]
    assert k_near > k_flip, (k_near, k_flip)
    assert k_flip < 0, f"an inverted labeller should score below zero: {k_flip}"

def test_abstentions_reported_separately():
    a = {1: -2, 2: None, 3: 1, 4: None}
    b = {1: -2, 2: None, 3: None, 4: 0}
    r = agreement.compare(a, b)
    assert r["n_compared"] == 1
    assert r["both_abstained"] == 1 and r["only_a_scored"] == 1 and r["only_b_scored"] == 1

def test_worst_disagreements_are_surfaced():
    a = {1: -2, 2: 0, 3: 1}
    b = {1: 2, 2: 0, 3: 1}
    r = agreement.compare(a, b)
    assert r["worst"] and r["worst"][0]["item"] == 1 and r["worst"][0]["gap"] == 4

# -------------------------------------------------------------- calibration --
def _model_with(bias, noise=0.0, per_outlet=None, seed=11):
    r = random.Random(seed)
    gold, model, groups = {}, {}, {}
    outs = ["mail", "sky", "guardian", "i"]
    for i in range(400):
        o = outs[i % 4]
        g = (i % 5) - 2
        b = bias + (per_outlet or {}).get(o, 0.0)
        gold[i], groups[i] = g, o
        model[i] = max(-2, min(2, g + b + (r.gauss(0, noise) if noise else 0)))
    return model, gold, groups

def test_a_constant_skew_is_found_and_declared_subtractable():
    m, g, grp = _model_with(bias=-0.4, noise=0.2)
    c = calibrate.calibrate(m, g, grp)
    assert -0.55 < c["overall"]["bias"] < -0.25, c["overall"]
    assert c["skew"]["constant_enough_to_subtract"] is True, c["skew"]
    assert "Subtract" in calibrate.report(c)

def test_a_skew_that_differs_by_paper_is_refused():
    m, g, grp = _model_with(bias=0.0, noise=0.1,
                            per_outlet={"mail": -0.8, "guardian": 0.8})
    c = calibrate.calibrate(m, g, grp)
    assert c["skew"]["constant_enough_to_subtract"] is False, c["skew"]
    assert c["skew"]["spread_between_groups"] > 1.0
    txt = calibrate.report(c)
    assert "Do not subtract" in txt and "unfair to particular titles" in txt

def test_noise_alone_does_not_look_like_skew():
    m, g, grp = _model_with(bias=0.0, noise=0.9)
    c = calibrate.calibrate(m, g, grp)
    assert abs(c["overall"]["bias"]) < 0.2, c["overall"]
    assert c["overall"]["mae"] > 0.3

def test_offset_stability_flags_a_moving_gap():
    steady = {k: [(1.0, 1.4)] * 30 for k in ("news", "comment")}
    moving = {"news": [(1.0, 1.9)] * 30, "comment": [(1.0, 1.05)] * 30}
    assert calibrate.offset_stability(steady)["_verdict"]["stable"] is True
    assert calibrate.offset_stability(moving)["_verdict"]["stable"] is False

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try: fn(); print(f"  ok   {n}")
            except AssertionError as e: fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e: fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
