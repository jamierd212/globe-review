"""How much two labellers agree.

Raw agreement flatters everybody. If most coverage is negative, two people who
both say "−1 to everything" agree 70% of the time and have told you nothing.
So the headline number is chance-corrected.

The scale is ordered — −2 is nearer −1 than it is to +2 — so a −2 against a −1
is a near miss and a −2 against a +2 is a disaster. Weighted kappa scores it
that way, by penalising each disagreement by the square of the gap.

Rough reading of the result:

    below 0.4   the rubric is ambiguous. Fix the rubric, not the labellers.
    0.4 - 0.6   usable, with the disagreements worth reading one by one
    0.6 - 0.8   good for this kind of judgement
    above 0.8   suspicious. Check the two people did not confer.

Abstentions are counted separately, not folded in. One person abstaining and
the other scoring −2 is a different kind of disagreement from −2 against +1,
and averaging them together hides both.
"""

from collections import defaultdict

SCALE = [-2, -1, 0, 1, 2]


def _weights(scale):
    k = len(scale) - 1
    return {(i, j): ((i - j) ** 2) / (k ** 2)
            for i in range(len(scale)) for j in range(len(scale))}


def weighted_kappa(pairs, scale=SCALE):
    """pairs: [(a, b)] of scores from two labellers, abstentions removed."""
    if not pairs:
        return None
    idx = {v: i for i, v in enumerate(scale)}
    n = len(pairs)
    w = _weights(scale)

    observed = defaultdict(int)
    ma, mb = defaultdict(int), defaultdict(int)
    for a, b in pairs:
        observed[(idx[a], idx[b])] += 1
        ma[idx[a]] += 1
        mb[idx[b]] += 1

    num = sum(w[(i, j)] * c for (i, j), c in observed.items())
    den = sum(w[(i, j)] * ma[i] * mb[j] / n
              for i in range(len(scale)) for j in range(len(scale)))
    if den == 0:
        return 1.0 if num == 0 else 0.0
    return 1 - num / den


def compare(a_labels, b_labels, scale=SCALE):
    """a_labels/b_labels: {item_id: score or None}

    Returns the numbers plus the worst disagreements, which are the ones to
    read. Every rubric problem found so far turned up in that list rather than
    in the summary figures.
    """
    ids = sorted(set(a_labels) & set(b_labels))
    both, only_a, only_b, neither = [], 0, 0, 0
    for i in ids:
        a, b = a_labels[i], b_labels[i]
        if a is None and b is None:
            neither += 1
        elif a is None:
            only_b += 1
        elif b is None:
            only_a += 1
        else:
            both.append((i, a, b))

    pairs = [(a, b) for _, a, b in both]
    exact = sum(1 for a, b in pairs if a == b)
    within1 = sum(1 for a, b in pairs if abs(a - b) <= 1)
    signs = sum(1 for a, b in pairs
                if (a > 0) == (b > 0) and (a < 0) == (b < 0))

    worst = sorted(both, key=lambda t: -abs(t[1] - t[2]))[:20]

    return {
        "n_compared": len(pairs),
        "n_items": len(ids),
        "kappa": None if not pairs else round(weighted_kappa(pairs, scale), 3),
        "exact": None if not pairs else round(exact / len(pairs), 3),
        "within_1": None if not pairs else round(within1 / len(pairs), 3),
        "same_sign": None if not pairs else round(signs / len(pairs), 3),
        "both_abstained": neither,
        "only_a_scored": only_a,
        "only_b_scored": only_b,
        "worst": [{"item": i, "a": a, "b": b, "gap": abs(a - b)}
                  for i, a, b in worst if abs(a - b) > 0],
    }


def report(r):
    if not r["n_compared"]:
        return "nothing to compare"
    lines = [
        f"compared {r['n_compared']} of {r['n_items']} items",
        f"  weighted kappa   {r['kappa']}",
        f"  exact match      {r['exact']:.0%}",
        f"  within 1 point   {r['within_1']:.0%}",
        f"  same sign        {r['same_sign']:.0%}",
        f"  abstentions      both {r['both_abstained']}, "
        f"only A {r['only_a_scored']}, only B {r['only_b_scored']}",
    ]
    k = r["kappa"]
    if k is not None:
        lines.append("  -> " + (
            "the rubric is ambiguous; fix the rubric before the labellers" if k < 0.4
            else "usable, but read the disagreements" if k < 0.6
            else "good for this kind of judgement" if k < 0.8
            else "unusually high - check they did not confer"))
    if r["worst"]:
        lines.append("\n  biggest disagreements (read these):")
        for w in r["worst"][:10]:
            lines.append(f"    item {w['item']}: A={w['a']:+d} B={w['b']:+d}")
    return "\n".join(lines)
