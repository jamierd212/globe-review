"""The globe's geometry. The claims the plan makes about the picture are
checked here rather than asserted: shapes get the area they should, none
vanishes, and distance from the centre still means what it says."""
import os, sys, math, random, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from frames import fit as F

def make(n=40, seed=3):
    r = random.Random(seed)
    return [{"id": i, "angle": r.uniform(-math.pi, math.pi),
             "rank_key": -r.randint(2, 14),        # -(outlets covering it)
             "area": r.uniform(20, 480)} for i in range(n)]

# --- the exact-interval trick has to agree with brute force, or nothing else
#     in this file means anything -----------------------------------------
def test_row_spans_agree_with_checking_every_point():
    r = random.Random(11)
    sx = [r.uniform(-.7,.7) for _ in range(9)]
    sy = [r.uniform(-.7,.7) for _ in range(9)]
    w  = [r.uniform(-.3,.3) for _ in range(9)]
    for y in (-0.8, -0.3, 0.0, 0.25, 0.61, 0.93):
        spans = F.row_spans(sx, sy, w, y)
        xr = math.sqrt(1 - y*y)
        wrong = 0
        for k in range(400):
            x = -xr + (k + 0.5) * (2*xr/400)
            brute = min(range(9), key=lambda i: (x-sx[i])**2 + (y-sy[i])**2 - w[i])
            owner = next((c for c, a, b in spans if a <= x <= b), None)
            if owner != brute: wrong += 1
        assert wrong <= 2, f"y={y}: {wrong}/400 points owned by the wrong shape"

def test_areas_sum_to_one():
    L = F.fit(make(12), rows=200, relax=120, iters=30)
    a = F.areas(L["sx"], L["sy"], L["w"], rows=200)
    assert abs(sum(a) - 1.0) < 1e-9, sum(a)

# --- the three claims ---------------------------------------------------
def test_every_shape_gets_close_to_the_area_it_should():
    L = F.fit(make(40), rows=360, relax=400, iters=120)
    c = F.check(L, rows=360)
    assert not c["empty_shapes"], c["empty_shapes"]
    # the prototype managed 1.4% by counting points; exact areas beat that
    assert c["worst_area_error"] < 0.01, c["worst_area_error"]
    assert c["mean_area_error"] < 0.003, c["mean_area_error"]

def test_no_shape_vanishes_when_one_story_takes_a_third_of_the_news():
    """A realistic dominant story - a Budget, a general election night."""
    items = make(30)
    items[0]["area"] = sum(i["area"] for i in items[1:]) * 0.5
    L = F.fit(items, rows=360, relax=400, iters=120)
    c = F.check(L, rows=360)
    assert not c["empty_shapes"], c["empty_shapes"]
    assert c["worst_area_error"] < 0.02, c["worst_area_error"]

def test_nothing_vanishes_even_at_a_silly_ratio():
    """300:1 should never happen - the live-set floor exists to stop it - but
    if it does, shapes must not disappear. This is what the stuck-shape rescue
    is for, and without it two shapes are lost here."""
    items = make(30)
    items[0]["area"] = 6000
    for it in items[1:]: it["area"] = random.Random(1).uniform(20, 60)
    L = F.fit(items, rows=360, relax=400, iters=120)
    assert not F.check(L, rows=360)["empty_shapes"]

def test_distance_from_the_centre_still_means_something():
    items = make(40)
    L = F.fit(items, rows=300, relax=400, iters=60)
    rho = F.radial_order(L, [i["rank_key"] for i in items])
    assert rho is not None and rho > 0.8, rho

# --- warm start ---------------------------------------------------------
def test_refit_keeps_shapes_where_they_were():
    items = make(30)
    a = F.fit(items, rows=300, relax=400, iters=60)
    nudged = [dict(i, area=i["area"] * (1 + 0.03 * math.sin(i["id"]))) for i in items]
    b = F.refit(a, nudged, rows=300)
    moves = [math.hypot(b["sx"][k]-a["sx"][k], b["sy"][k]-a["sy"][k])
             for k in range(a["n"])]
    assert max(moves) < 0.06, f"shapes jumped: worst {max(moves):.3f}"
    assert not F.check(b, rows=300)["empty_shapes"]

def test_refit_is_much_faster_than_starting_over():
    items = make(30)
    a = F.fit(items, rows=300, relax=400, iters=60)
    t0 = time.time(); F.fit(items, rows=300, relax=400, iters=60); cold = time.time()-t0
    t0 = time.time(); F.refit(a, items, rows=300); warm = time.time()-t0
    assert warm < cold / 3, f"cold {cold:.2f}s warm {warm:.2f}s"

def test_a_story_that_arrives_starts_small_and_does_not_shove_everything():
    items = make(20)
    a = F.fit(items, rows=300, relax=400, iters=60)
    newcomer = {"id": 999, "angle": 0.4, "rank_key": -3, "area": 30}
    b = F.refit(a, items + [newcomer], rows=300)
    moves = [math.hypot(b["sx"][k]-a["sx"][k], b["sy"][k]-a["sy"][k]) for k in range(20)]
    assert max(moves) < 0.10, f"an arrival reshuffled the globe: {max(moves):.3f}"
    assert b["ids"][-1] == 999

def test_a_story_that_leaves_does_not_reshuffle_the_rest():
    """Neighbours SHOULD close the gap - that is the picture being honest
    about the space freeing up. What must not happen is the whole globe
    rearranging, so the test is on the median, not the worst."""
    items = make(20)
    a = F.fit(items, rows=300, relax=400, iters=60)
    kept = [i for i in items if i["id"] != 7]
    b = F.refit(a, kept, rows=300)
    moves = sorted(math.hypot(b["sx"][k]-a["sx"][a["ids"].index(it["id"])],
                              b["sy"][k]-a["sy"][a["ids"].index(it["id"])])
                   for k, it in enumerate(kept))
    median = moves[len(moves)//2]
    assert median < 0.08, f"the whole globe moved: median {median:.3f}"
    assert moves[-1] < 0.25, f"a neighbour flew across the disc: {moves[-1]:.3f}"

def test_empty_input_is_not_a_crash():
    assert F.fit([])["n"] == 0 and F.refit({"ids":[],"sx":[],"sy":[],"w":[]}, [])["n"] == 0

# --- labels -------------------------------------------------------------
def test_labels_stay_inside_the_disc_and_clear_of_each_other():
    """Both failures this guards against were invisible to the maths and
    obvious to the eye: text drawn off the canvas entirely (a shadowed
    variable put every label at the wrong origin), and labels overlapping."""
    import json as _json
    from frames import labels as LB, draw
    items = make(30)
    for k, it in enumerate(items):
        it["name"] = ["Reform receives record crypto billionaire donations",
                      "Boris Johnson escapes drone strike in Ukraine",
                      "Tech leaders call for AI development slowdown",
                      "Sweden holds parliamentary election"][k % 4]
        it["stance"] = ((k % 5) - 2) / 1.0
    layout = F.fit(items, rows=300, relax=300, iters=80)
    R = (draw.SIZE - 2 * draw.PAD) / 2.0
    cx = cy = draw.SIZE / 2.0
    labs = LB.place(items, layout, R)
    assert len(labs) >= len(items) * 0.6, f"only {len(labs)}/{len(items)} labelled"

    # One box per LINE, compared only across labels. Two invariants, and
    # neither is the obvious one:
    #   lines of the same label overlap by design - that is leading
    #   bounding boxes of two labels may overlap without any text touching,
    #     because a short line of one can sit beside a long line of another
    # So the thing that must be true is that no rendered line touches a
    # rendered line belonging to a different shape.
    lines = []
    for li, lab in enumerate(labs):
        for txt, lx, ly in lab["lines"]:
            fs = lab["fs"] * 0.66 if txt == lab["tail"] else lab["fs"]
            w = LB.text_width(txt, fs)
            x, y = cx + lx * R, cy - ly * R
            for ex in (x - w / 2, x + w / 2):
                assert math.hypot(ex - cx, y - cy) <= R + 2, \
                    f"{txt!r} is outside the disc"
            lines.append((li, x, y, w / 2, fs * 0.5, txt))
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            a, b = lines[i], lines[j]
            if a[0] == b[0]:
                continue                      # same label
            assert not (abs(a[1] - b[1]) < a[3] + b[3]
                        and abs(a[2] - b[2]) < a[4] + b[4]), \
                f"text overlaps: {a[5][:28]!r} / {b[5][:28]!r}"

def test_the_width_estimate_is_close_to_the_real_font():
    """Measured in a browser against bold Helvetica at 20px. If this drifts
    the fitting silently starts overflowing shapes."""
    from frames.labels import text_width
    for txt, real in (("Thirlwall inquiry report on Lucy", 14.737),
                      ("Boris Johnson escapes", 11.130),
                      ("Tech leaders call", 7.978),
                      ("US confirms space weapons", 13.629)):
        mine = text_width(txt, 1.0)
        assert 0.97 < mine / real < 1.05, f"{txt!r}: {mine:.2f} vs {real:.2f}"

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            t0=time.time()
            try: fn(); print(f"  ok   {n}  ({time.time()-t0:.1f}s)")
            except AssertionError as e: fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e: fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
