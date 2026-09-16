"""Story identity, tested with synthetic vectors - no embedding model needed.
The failure this guards against does not look like a crash. It looks like an
implausibly volatile news cycle, which is why it needs a test rather than an
eyeball."""
import os, sys, math, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cluster import identity as I

def vec(seed, dim=16, noise=0.0):
    r = random.Random(seed)
    v = [r.gauss(0, 1) for _ in range(dim)]
    if noise:
        rn = random.Random(seed * 7919 + 13)
        v = [x + rn.gauss(0, noise) for x in v]
    n = math.sqrt(sum(x*x for x in v))
    return [x/n for x in v]

def test_same_story_keeps_its_id_across_days():
    stories = []
    ids = []
    for day in range(5):
        clusters = [{"key": f"c{day}", "centroid": vec(1, noise=0.18), "n": 6}]
        a, stories = I.assign(clusters, stories, day)
        ids.append(a[f"c{day}"])
    assert len(set(ids)) == 1, f"story changed id across days: {ids}"

def test_new_story_gets_a_new_id():
    stories = []
    a1, stories = I.assign([{"key":"a","centroid":vec(1),"n":5}], stories, 0)
    a2, stories = I.assign([{"key":"a","centroid":vec(1,noise=0.15),"n":5},
                            {"key":"b","centroid":vec(99),"n":4}], stories, 1)
    assert a2["a"] == a1["a"]
    assert a2["b"] != a1["a"]

def test_story_goes_dormant_then_closes_and_never_returns():
    stories = []
    a, stories = I.assign([{"key":"a","centroid":vec(1),"n":5}], stories, 0)
    sid = a["a"]
    for day in (1, 2):
        _, stories = I.assign([{"key":"x","centroid":vec(50),"n":3}], stories, day)
        assert next(s for s in stories if s["id"]==sid)["status"] == "dormant"
    # comes back while still dormant -> same id
    a2, stories = I.assign([{"key":"a2","centroid":vec(1,noise=0.15),"n":5}], stories, 3)
    assert a2["a2"] == sid, "a dormant story must be rematchable"
    # now leave it long enough to close
    for day in range(4, 4 + I.DORMANT_DAYS + 2):
        _, stories = I.assign([{"key":"x","centroid":vec(50),"n":3}], stories, day)
    assert next(s for s in stories if s["id"]==sid)["status"] == "closed"
    a3, stories = I.assign([{"key":"a3","centroid":vec(1,noise=0.15),"n":5}],
                           stories, 4 + I.DORMANT_DAYS + 2)
    assert a3["a3"] != sid, "a closed story must not be resurrected"

def basis(i, dim=16):
    v = [0.0]*dim; v[i] = 1.0; return v

def mix(a, b, wa, wb, dim=16):
    v = [wa*x + wb*y for x, y in zip(basis(a,dim), basis(b,dim))]
    n = math.sqrt(sum(x*x for x in v))
    return [x/n for x in v]

def test_two_clusters_cannot_take_the_same_story():
    """Both clusters resemble the story enough to match it (0.8 > MATCH) but
    not each other enough to be merged afterwards (0.64 < MERGE), which is the
    case where the one-to-one rule actually has to hold."""
    stories = []
    _, stories = I.assign([{"key":"a","centroid":basis(0),"n":5}], stories, 0)
    x, y = mix(0, 1, 0.8, 0.6), mix(0, 2, 0.8, 0.6)
    assert I.cosine(x, basis(0)) > I.MATCH and I.cosine(x, y) < I.MERGE
    a, stories = I.assign([{"key":"x","centroid":x,"n":5},
                           {"key":"y","centroid":y,"n":5}], stories, 1)
    assert a["x"] != a["y"], "one story claimed by two clusters"

def test_same_day_near_duplicates_end_up_merged():
    """Pinning the behaviour the previous test surfaced: if the clusterer hands
    us two clusters that are really the same thing, they are assigned to
    different stories and then merged back into the older id. That is a
    clustering fault being cleaned up, and it should stay cleaned up."""
    stories = []
    a0, stories = I.assign([{"key":"a","centroid":basis(0),"n":5}], stories, 0)
    near = mix(0, 1, 0.99, 0.14)
    a, stories = I.assign([{"key":"x","centroid":basis(0),"n":5},
                           {"key":"y","centroid":near,"n":5}], stories, 1)
    assert I.cosine(basis(0), near) >= I.MERGE
    assert a["x"] == a["y"] == a0["a"], (a, a0)

def test_converged_stories_merge_into_the_older_id():
    stories = []
    a0, stories = I.assign([{"key":"a","centroid":vec(1),"n":5}], stories, 0)
    old = a0["a"]
    a1, stories = I.assign([{"key":"a","centroid":vec(1,noise=0.1),"n":5},
                            {"key":"b","centroid":vec(2),"n":5}], stories, 1)
    younger = a1["b"]
    assert younger != old
    # day 2: both clusters land on the same point
    same = vec(1, noise=0.05)
    a2, stories = I.assign([{"key":"a","centroid":same,"n":5},
                            {"key":"b","centroid":same,"n":5}], stories, 2)
    merged = [s for s in stories if s["status"] == "merged"]
    if merged:
        assert merged[0]["merged_into"] == min(old, younger)
        assert all(v != merged[0]["id"] for v in a2.values())

def test_drift_follows_a_developing_story_without_teleporting():
    """A story that evolves should stay one story, and its centroid should lag."""
    stories = []
    start = vec(1)
    a, stories = I.assign([{"key":"c0","centroid":start,"n":5}], stories, 0)
    sid = a["c0"]
    end = vec(2)
    for day in range(1, 9):
        t = day / 8.0
        drifting = [s + (e - s) * t for s, e in zip(start, end)]
        a, stories = I.assign([{"key":f"c{day}","centroid":drifting,"n":5}], stories, day)
        assert a[f"c{day}"] == sid, f"lost the thread on day {day}"
    s = next(x for x in stories if x["id"] == sid)
    assert I.cosine(s["centroid"], end) < 0.999, "centroid teleported to today"
    assert I.cosine(s["centroid"], end) > I.cosine(s["centroid"], start), "centroid failed to follow"

def test_lifetimes_reports_a_plausible_churn():
    """A week of news: a few long runners, mostly one-day stories."""
    stories = []
    runners = [vec(i) for i in range(3)]
    for day in range(7):
        clusters = [{"key": f"r{i}", "centroid": vec(i, noise=0.15), "n": 8}
                    for i in range(3)]
        clusters += [{"key": f"n{day}_{j}", "centroid": vec(1000 + day*10 + j), "n": 3}
                     for j in range(5)]
        _, stories = I.assign(clusters, stories, day)
    lt = I.lifetimes(stories)
    assert lt["max"] >= 7, lt
    assert lt["median"] == 1, f"most stories should be short: {lt}"
    assert lt["n"] > 30, lt

def test_a_running_story_is_never_renamed():
    """naming.md: a name is written once and left alone. Someone watching a
    line move needs it to keep meaning the same thing - and the provisional
    name is one paper's headline, so re-applying it puts that paper's framing
    back on everybody's coverage."""
    import os as _os, sys as _sys, tempfile
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from ingest import db
    import inspect
    from cluster import run as CR
    src = inspect.getsource(CR.run)
    upd = src[src.index("if row:"):src.index("else:", src.index("if row:"))]
    assert "name=?" not in upd, \
        "the update path sets name - clustering must not rename a live story"
    assert "name" in src[src.index("else:", src.index("if row:")):], \
        "a newly born story still needs a name"

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try: fn(); print(f"  ok   {n}")
            except AssertionError as e: fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e: fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
