"""Clustering, tested on made-up headlines so the right answer is known.
The failure that matters most - the answer changing with the order articles
arrive in - would never show up by eye."""
import os, sys, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cluster import group as G, embed

# three real-looking stories, told slightly differently by each paper
STORIES = {
  "sanctions": [
    ("guardian", "UK imposes sanctions on Israeli settlers in the West Bank"),
    ("times",    "Britain sanctions West Bank settlers over violence"),
    ("sky",      "UK announces sanctions against Israeli settlers"),
    ("mail",     "Sanctions on Israeli settlers announced by Foreign Office"),
    ("mirror",   "Britain hits West Bank settlers with new sanctions"),
  ],
  "boats": [
    ("express",  "Fury as small boat crossings hit record for the month"),
    ("mail",     "Record small boat crossings spark anger at Home Office"),
    ("sky",      "Small boat crossings reach monthly record, figures show"),
    ("guardian", "Monthly record for small boat Channel crossings"),
  ],
  "rates": [
    ("ft",       "Bank of England holds interest rates at 4 per cent"),
    ("times",    "Interest rates held at 4% by Bank of England"),
    ("sky",      "Bank of England keeps interest rates on hold"),
  ],
}

def build():
    items, truth = [], {}
    i = 0
    texts = [t for rows in STORIES.values() for _, t in rows]
    vecs = embed.embed_batch(texts)
    k = 0
    for name, rows in STORIES.items():
        for outlet, text in rows:
            items.append({"id": i, "outlet_id": outlet, "vec": vecs[k],
                          "published_at": f"2026-09-10T0{i%9}:00:00Z", "text": text})
            truth[i] = name
            i += 1; k += 1
    return items, truth

def purity(stories, truth):
    """Share of articles sitting in a group whose majority is their own story."""
    total = correct = 0
    for s in stories:
        labs = [truth[m] for m in s["members"]]
        best = max(set(labs), key=labs.count)
        correct += labs.count(best); total += len(labs)
    return correct / total if total else 0

def test_separate_stories_do_not_merge():
    items, truth = build()
    stories, pending = G.group(items, threshold=0.30)
    assert len(stories) >= 3, [s["outlets"] for s in stories]
    assert purity(stories, truth) >= 0.9, purity(stories, truth)

def test_result_does_not_depend_on_arrival_order():
    items, truth = build()
    a, _ = G.group(items, threshold=0.30)
    sig_a = sorted(tuple(s["members"]) for s in a)
    for seed in (1, 2, 3, 4, 5):
        shuffled = items[:]
        random.Random(seed).shuffle(shuffled)
        b, _ = G.group(shuffled, threshold=0.30)
        sig_b = sorted(tuple(s["members"]) for s in b)
        assert sig_a == sig_b, f"order changed the grouping (seed {seed})"

def test_one_paper_alone_is_not_a_story_yet():
    items, _ = build()
    vec = embed.embed_batch(["Council approves new leisure centre in Bolton"])[0]
    items.append({"id": 99, "outlet_id": "i", "vec": vec,
                  "published_at": "2026-09-10T09:00:00Z"})
    stories, pending = G.group(items, threshold=0.30)
    assert any(99 in p["members"] for p in pending), "solo article became a story"
    assert not any(99 in s["members"] for s in stories)

def test_a_too_low_threshold_mixes_stories_together():
    """Set it too low and groups stop being about one thing. That shows up as
    purity collapsing and one group swallowing most of the day - not
    necessarily as a single blob, because two headlines about different things
    can point in opposite directions and still refuse to merge."""
    items, truth = build()
    good, _ = G.group(items, threshold=0.30)
    bad, _ = G.group(items, threshold=0.01)
    assert purity(bad, truth) < purity(good, truth) - 0.15, \
        (purity(bad, truth), purity(good, truth))
    assert G.spread(bad)["biggest_share"] > G.spread(good)["biggest_share"] + 0.15, \
        (G.spread(bad), G.spread(good))

def test_a_too_high_threshold_shows_up_as_all_singletons():
    items, _ = build()
    stories, pending = G.group(items, threshold=0.999)
    assert not stories, "nothing should cluster at threshold 0.999"
    assert len(pending) == len(items)

def test_empty_input_is_not_a_crash():
    assert G.group([]) == ([], [])

# ------------------------------------------------------------- embedding ---
def test_same_text_gives_the_same_vector_every_run():
    a = embed.hashed("Bank of England holds interest rates")
    b = embed.hashed("Bank of England holds interest rates")
    assert a == b

def test_related_headlines_are_closer_than_unrelated_ones():
    v = embed.embed_batch([
        "UK sanctions Israeli settlers in the West Bank",
        "Britain sanctions West Bank settlers",
        "Bank of England holds interest rates at 4 per cent"])
    assert G.cosine(v[0], v[1]) > G.cosine(v[0], v[2])

def test_word_pairs_are_used():
    assert "small_boats" in embed.tokens("Record small boats crossing")

def test_the_default_threshold_actually_produces_stories():
    """The bug this guards against is silent: a threshold copied from the
    other vectoriser gives an empty globe and no error message."""
    items, truth = build()
    stories, pending = G.group(items)          # all defaults
    assert stories, ("default threshold produced no stories at all",
                     G.THRESHOLD)
    assert purity(stories, truth) >= 0.9, purity(stories, truth)
    assert len(stories) >= 2, [s["outlets"] for s in stories]

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try: fn(); print(f"  ok   {n}")
            except AssertionError as e: fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e: fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
