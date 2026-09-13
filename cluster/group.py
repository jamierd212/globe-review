"""Grouping today's articles into stories.

A story is one event that several papers are covering. The Times, the Mirror
and Sky all writing about the same sanctions announcement is one shape on the
globe, not three.

The method is deliberately plain: put each article with the nearest group if
it is close enough, otherwise start a new group. At a few thousand articles a
day nothing fancier earns its keep.

The one thing that does need care is **order**. A first pass like that gives
different answers depending on which article it sees first — so whichever
paper's feed happened to be polled first would change the shape of the globe.
That is not a small bug, it is an instrument that reads differently depending
on the weather. So after the first pass every article is moved to whichever
group it now fits best, repeatedly, until nothing moves. The answer stops
depending on the order it arrived in, and there is a test that proves it.

A group covered by only one paper is not a story yet. It is held back, and it
becomes one tomorrow if somebody else picks it up.
"""

import math

# How similar two articles must be to sit in the same story.
#
# This number is NOT portable between the two vectorisers, and getting that
# wrong is silent: too high and every article sits alone, so the globe is empty
# and nothing says why. Measured on the test set, the built-in word-counting
# vectoriser works around 0.15-0.30 and produces nothing at all above 0.45.
# Sentence embeddings sit much higher because unrelated text still scores
# around 0.3 with them.
#
# Both figures are starting points. Set them properly once feeds are running,
# against the median story lifetime rather than by eye.
# Measured on 642 real articles from 13 papers, 13 Sept 2026:
#   0.14 -> 116 stories, the big national ones all correctly joined
#           (Boris Johnson's train, 10 outlets), but some false merges
#   0.20 ->  49 stories, cleaner, some real stories split in two
#   0.28 ->  23 stories, too tight - only the very biggest survive
# 0.16 is the compromise while the word-counting vectoriser is in use. The
# false merges are its limit, not the threshold's: it cannot tell that a Sun
# agony column and an FT piece share only common words. Sentence embeddings
# are the fix, and the number will need setting again for them.
THRESHOLDS = {"hashed": 0.16, "sentence": 0.62}
THRESHOLD = THRESHOLDS["hashed"]

# a story needs this many different papers before it goes on the globe
MIN_OUTLETS = 2

MAX_PASSES = 8


def cosine(a, b):
    n = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else n / (na * nb)


def _centroid(vecs):
    if not vecs:
        return []
    dim = len(vecs[0])
    c = [0.0] * dim
    for v in vecs:
        for i, x in enumerate(v):
            c[i] += x
    n = math.sqrt(sum(x * x for x in c))
    return [x / n for x in c] if n else c


def group(items, threshold=THRESHOLD, min_outlets=MIN_OUTLETS,
          max_passes=MAX_PASSES):
    """items: [{id, outlet_id, vec, published_at?}]

    Returns (stories, pending) where each is a list of
    {key, members, outlets, centroid, n}. `pending` are the groups only one
    paper has so far.
    """
    if not items:
        return [], []

    # a fixed starting order so two runs of the same day match each other
    ordered = sorted(items, key=lambda i: (i.get("published_at") or "", i["id"]))

    assign = {}
    centroids = []          # list of vectors, index = group number
    members = []            # list of lists of item ids

    for it in ordered:
        best, best_sim = -1, threshold
        for gi, c in enumerate(centroids):
            s = cosine(it["vec"], c)
            if s >= best_sim:
                best, best_sim = gi, s
        if best < 0:
            centroids.append(list(it["vec"]))
            members.append([it["id"]])
            assign[it["id"]] = len(centroids) - 1
        else:
            members[best].append(it["id"])
            assign[it["id"]] = best
            vecs = [x["vec"] for x in ordered if assign.get(x["id"]) == best]
            centroids[best] = _centroid(vecs)

    by_id = {i["id"]: i for i in ordered}

    # Move everything to wherever it fits best now, until nothing moves. This
    # is what removes the dependence on arrival order.
    for _ in range(max_passes):
        centroids = [_centroid([by_id[m]["vec"] for m in ms]) if ms else []
                     for ms in members]
        moved = 0
        new_members = [[] for _ in members]
        for it in ordered:
            best, best_sim = -1, threshold
            for gi, c in enumerate(centroids):
                if not c:
                    continue
                s = cosine(it["vec"], c)
                if s >= best_sim:
                    best, best_sim = gi, s
            if best < 0:
                centroids.append(list(it["vec"]))
                new_members.append([it["id"]])
                best = len(centroids) - 1
            else:
                new_members[best].append(it["id"])
            if assign.get(it["id"]) != best:
                moved += 1
                assign[it["id"]] = best
        members = new_members
        if not moved:
            break

    out = []
    for gi, ms in enumerate(members):
        if not ms:
            continue
        outlets = sorted({by_id[m]["outlet_id"] for m in ms})
        # the key names the group by its contents, so it does not depend on
        # what order the groups happened to come out in
        out.append({
            "key": f"g{min(ms)}",
            "members": sorted(ms),
            "outlets": outlets,
            "centroid": _centroid([by_id[m]["vec"] for m in ms]),
            "n": len(ms),
        })

    out.sort(key=lambda g: (-g["n"], g["key"]))
    stories = [g for g in out if len(g["outlets"]) >= min_outlets]
    pending = [g for g in out if len(g["outlets"]) < min_outlets]
    return stories, pending


def spread(stories):
    """A quick read on whether the threshold is sane.

    One enormous group means it is too low. Every article in its own group
    means it is too high. Neither is obvious from looking at the output.
    """
    if not stories:
        return {}
    sizes = sorted((s["n"] for s in stories), reverse=True)
    total = sum(sizes)
    return {
        "stories": len(stories),
        "articles": total,
        "biggest": sizes[0],
        "biggest_share": round(sizes[0] / total, 3),
        "median_size": sizes[len(sizes) // 2],
        "singletons": sum(1 for s in sizes if s == 1),
        "median_outlets": sorted(len(s["outlets"]) for s in stories)[len(stories) // 2],
    }
