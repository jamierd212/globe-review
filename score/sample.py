"""Picking what goes in the gold set.

The gold set has one job: tell us how wrong the scoring is, and whether it is
wrong in the same direction for everybody or worse for some papers than
others. That second question decides the sampling.

If we sampled at random, MailOnline would be a third of the set and the i
paper would be four items, and we could say nothing about whether the scores
are fair to the i paper. So: roughly equal numbers per outlet, and spread
across subjects within each outlet.

Random error washes out over a few hundred items. Bias does not wash out at
all, and bias is what we are looking for.
"""

import random
from collections import defaultdict

# below this, a per-outlet number means nothing and should not be published
MIN_PER_OUTLET = 20


def stratified(items, n=300, per_outlet_min=MIN_PER_OUTLET, seed=1):
    """items: [{id, outlet_id, issue_id, ...}] -> a list of the same dicts.

    Equal share per outlet, then spread across subjects inside each outlet.
    Seeded, so the same articles come out every time and two people can label
    the same set without coordinating.
    """
    rng = random.Random(seed)
    by_outlet = defaultdict(list)
    for it in items:
        by_outlet[it["outlet_id"]].append(it)

    outlets = sorted(by_outlet)
    if not outlets:
        return []
    share = max(per_outlet_min, n // len(outlets))

    picked = []
    for oid in outlets:
        pool = by_outlet[oid]
        # spread across subjects: take one from each in turn until we have
        # enough, so a week dominated by one story does not fill the quota
        by_issue = defaultdict(list)
        for it in pool:
            by_issue[it.get("issue_id") or "_none"].append(it)
        for k in by_issue:
            rng.shuffle(by_issue[k])
        order = sorted(by_issue, key=lambda k: -len(by_issue[k]))
        take, i = [], 0
        while len(take) < share and any(by_issue[k] for k in order):
            k = order[i % len(order)]
            if by_issue[k]:
                take.append(by_issue[k].pop())
            i += 1
        picked.extend(take)

    rng.shuffle(picked)          # so labellers do not see one paper in a run
    return picked


def coverage(sample):
    """What the sample actually contains, so its limits can be stated rather
    than discovered later."""
    by_outlet, by_issue = defaultdict(int), defaultdict(int)
    for it in sample:
        by_outlet[it["outlet_id"]] += 1
        by_issue[it.get("issue_id") or "_none"] += 1
    thin = [o for o, c in by_outlet.items() if c < MIN_PER_OUTLET]
    return {
        "n": len(sample),
        "outlets": dict(sorted(by_outlet.items())),
        "issues": dict(sorted(by_issue.items(), key=lambda kv: -kv[1])),
        "too_thin_to_report": sorted(thin),
    }
