"""Story identity across days.

A story is what the globe draws: one running event, named from the coverage.
"UK sanctions on Israeli settlements", not "Middle East". Most last days.
Some last a week. A few become permanent and get promoted to the taxonomy.

The hard part is not forming clusters, it is keeping their IDENTITY stable
overnight. A cluster that gets a new id every morning cannot be tracked, and
everything downstream - the line chart, the playback, the archive - depends on
a story being the same object today that it was yesterday. Getting this wrong
does not look like a bug. It looks like an extremely volatile news cycle.

Four rules do the work:

  match     today's cluster joins yesterday's story if the centroids are close
            enough. First-past-a-threshold, best match wins, one to one.
  drift     a matched story's centroid moves toward the new one but does not
            jump to it. A story evolves - the sanctions story acquires a vote,
            then a resignation - and hard-assigning the centroid each day lets
            it walk away from what it started as until it merges with something
            unrelated.
  dormancy  no coverage today does not kill a story. It goes dormant and can
            still be matched for a few days, because news comes back. Only
            then is it closed, and a closed story is never rematched.
  merge     two live stories that converge are merged into the OLDER id, so the
            longer thread survives. Splits are not attempted: a story that
            genuinely forks reads better as the old story dying and two new
            ones being born, and trying to model it properly costs far more
            than it returns.
"""

import math

# cosine similarity above which today's cluster is yesterday's story.
# Deliberately not tuned here: it has to be set against real embeddings, and
# the number that matters is measured, not guessed. Start at 0.72 and check
# what it does to the median story lifetime.
MATCH = 0.72

# how far a matched story's centroid moves toward today's. 1.0 = jump, which
# lets stories drift into each other; 0.0 = frozen, which loses the thread as
# the event develops.
DRIFT = 0.35

# similarity above which two LIVE stories are considered the same thing
MERGE = 0.86

# days without coverage before a dormant story is closed for good
DORMANT_DAYS = 4


def cosine(a, b):
    if not a or not b:
        return 0.0
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else num / (na * nb)


def blend(old, new, drift=DRIFT):
    return [o + (n - o) * drift for o, n in zip(old, new)]


def assign(clusters, stories, day, match=MATCH, merge=MERGE,
           dormant_days=DORMANT_DAYS, drift=DRIFT):
    """Map today's clusters onto yesterday's stories.

    clusters: [{key, centroid, n}]        - today, from the clusterer
    stories:  [{id, centroid, last_day, status, first_day}] - carried forward
    day:      an integer day index

    Returns (assignments, stories_out) where assignments maps cluster key ->
    story id, and stories_out is the updated story list including new births.
    """
    live = [s for s in stories if s["status"] != "closed"]

    # every (cluster, story) pair above threshold, best first. Greedy on a
    # sorted list of pairs is enough and, unlike per-cluster argmax, cannot
    # assign two clusters to the same story.
    pairs = []
    for c in clusters:
        for s in live:
            sim = cosine(c["centroid"], s["centroid"])
            if sim >= match:
                pairs.append((sim, c["key"], s["id"]))
    pairs.sort(reverse=True)

    taken_c, taken_s, assignments = set(), set(), {}
    for sim, ckey, sid in pairs:
        if ckey in taken_c or sid in taken_s:
            continue
        taken_c.add(ckey)
        taken_s.add(sid)
        assignments[ckey] = sid

    by_id = {s["id"]: s for s in stories}
    next_id = max([s["id"] for s in stories], default=0) + 1

    for c in clusters:
        if c["key"] in assignments:
            s = by_id[assignments[c["key"]]]
            s["centroid"] = blend(s["centroid"], c["centroid"], drift)
            s["last_day"] = day
            s["status"] = "live"
            s["n_articles"] = s.get("n_articles", 0) + c.get("n", 0)
        else:
            # a genuinely new story. This is the common case and it is supposed
            # to be: most of what the press runs today did not exist last week.
            s = {"id": next_id, "centroid": list(c["centroid"]),
                 "first_day": day, "last_day": day, "status": "live",
                 "n_articles": c.get("n", 0)}
            by_id[next_id] = s
            assignments[c["key"]] = next_id
            next_id += 1

    # anything not seen today goes dormant, then closed
    for s in by_id.values():
        if s["last_day"] == day:
            continue
        if s["status"] == "closed":
            continue
        s["status"] = "closed" if day - s["last_day"] > dormant_days else "dormant"

    out = _merge_converged(list(by_id.values()), assignments, merge, day)
    return assignments, out


def _merge_converged(stories, assignments, threshold, day):
    """Two live stories that have converged are the same story. Keep the older
    id so the longer thread survives - a reader following a line does not want
    it to change identity because a related story caught up with it."""
    live = sorted([s for s in stories if s["status"] == "live"],
                  key=lambda s: s["id"])
    dead = set()
    for i in range(len(live)):
        if live[i]["id"] in dead:
            continue
        for j in range(i + 1, len(live)):
            if live[j]["id"] in dead:
                continue
            if cosine(live[i]["centroid"], live[j]["centroid"]) >= threshold:
                keep, drop = live[i], live[j]
                keep["centroid"] = blend(keep["centroid"], drop["centroid"], 0.5)
                keep["n_articles"] = keep.get("n_articles", 0) + drop.get("n_articles", 0)
                keep["first_day"] = min(keep["first_day"], drop["first_day"])
                keep["last_day"] = day
                drop["status"] = "merged"
                drop["merged_into"] = keep["id"]
                dead.add(drop["id"])
                for k, v in list(assignments.items()):
                    if v == drop["id"]:
                        assignments[k] = keep["id"]
    return stories


def lifetimes(stories):
    """Distribution of story lifetimes in days. The number to watch: if the
    median is 1, identity matching is failing and every day is inventing a new
    news cycle. If nothing ever closes, the threshold is too loose and
    everything has merged into one blob."""
    lives = [s["last_day"] - s["first_day"] + 1
             for s in stories if s["status"] != "merged"]
    lives.sort()
    if not lives:
        return {}
    return {"n": len(lives), "median": lives[len(lives) // 2],
            "p90": lives[min(len(lives) - 1, int(len(lives) * 0.9))],
            "max": lives[-1], "singletons": sum(1 for x in lives if x == 1)}
