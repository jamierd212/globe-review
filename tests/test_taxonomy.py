"""Structural checks on the taxonomy, plus the direction regression itself
exercised against synthetic scores - including a deliberately inverted one,
because a test that only ever sees correct input proves nothing."""
import os, sys, json, re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from score import direction

TAX = direction.load_taxonomy()
OUT = direction.load_outlets()

def test_every_issue_has_a_stance_target():
    for i in TAX["issues"]:
        assert i.get("target"), i["id"]
        # the target must name a thing, not restate the label
        assert i["target"].strip().lower() != i["name"].strip().lower(), i["id"]
        assert len(i["target"].split()) >= 3, (i["id"], i["target"])

def test_ids_unique_and_slug_shaped():
    ids = [i["id"] for i in TAX["issues"]]
    assert len(ids) == len(set(ids)), "duplicate issue id"
    for i in ids:
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", i), i

def test_expectations_well_formed():
    ok = {"favourable", "hostile", "mixed"}
    for i in TAX["issues"]:
        e = i["expect"]
        assert e["left"] in ok and e["right"] in ok, i["id"]
        assert isinstance(e["regression"], bool), i["id"]
        if e["regression"]:
            # asserting a sign requires the two sides to actually differ
            assert direction.expected_sign(e) != 0, \
                f"{i['id']}: regression:true but left and right are the same"
            assert e.get("why"), f"{i['id']}: regression:true needs a why"

def test_cross_references_resolve():
    ids = {i["id"] for i in TAX["issues"]}
    for i in TAX["issues"]:
        for ex in i.get("excludes", []):
            for ref in re.findall(r"->\s*([a-z0-9-]+)", ex):
                assert ref in ids, f"{i['id']} excludes -> unknown issue '{ref}'"

def test_net_zero_is_the_right_way_round():
    """The inversion that was actually found. Guarded explicitly."""
    nz = next(i for i in TAX["issues"] if i["id"] == "net-zero-rollback")
    assert "weaken" in nz["target"].lower() or "delay" in nz["target"].lower()
    assert nz["expect"]["left"] == "hostile" and nz["expect"]["right"] == "favourable"
    assert direction.expected_sign(nz["expect"]) < 0   # left minus right is negative

def test_direction_passes_on_correct_scores():
    scores = _synth(invert=None)
    ok, rows = direction.check(scores)
    assert ok, direction.report(rows)
    assert any(r["verdict"] == "ok" for r in rows)

def test_direction_catches_an_inversion():
    scores = _synth(invert="net-zero-rollback")
    ok, rows = direction.check(scores)
    assert not ok
    bad = [r for r in rows if r["verdict"] == "INVERTED"]
    assert [r["issue"] for r in bad] == ["net-zero-rollback"], bad

def test_deadband_gives_inconclusive_not_failure():
    scores = []
    for i in TAX["issues"]:
        if not i["expect"]["regression"]: continue
        for o in OUT["outlets"]:
            scores.append({"issue_id": i["id"], "outlet_id": o["id"], "stance": 0.0})
    ok, rows = direction.check(scores)
    assert ok, "a flat week must not fail the build"
    assert all(r["verdict"] in ("INCONCLUSIVE", "TOO FEW") for r in rows), rows

def test_too_few_items_is_not_a_failure():
    scores = [{"issue_id": "eu-relations", "outlet_id": "guardian", "stance": 1.0}]
    ok, rows = direction.check(scores)
    assert ok
    assert {r["verdict"] for r in rows} == {"TOO FEW"}

def _synth(invert=None):
    """Scores that obey every stated expectation, optionally flipping one."""
    scores = []
    for i in TAX["issues"]:
        e = i["expect"]
        if not e["regression"]: continue
        sign = direction.expected_sign(e)
        if i["id"] == invert: sign = -sign
        for o in OUT["outlets"]:
            lean = o["leaning"]
            if abs(lean) < 0.15: continue          # centrists add no signal
            # left outlets get +sign, right outlets get -sign, scaled by leaning
            scores.append({"issue_id": i["id"], "outlet_id": o["id"],
                           "stance": round(-sign * lean * 1.5, 2)})
    return scores

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try: fn(); print(f"  ok   {n}")
            except AssertionError as e: fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e: fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
