"""Checking the model against the gold set.

Two different questions, and only the second one can sink the project.

  How noisy is it?  Scores wobble around the truth. A shape on the globe is an
                    average of thirty to a hundred articles, so wobble mostly
                    cancels out. Report it, don't panic about it.

  Is it skewed?     Every score sitting a third of a point low. This does NOT
                    cancel out. It shifts the whole picture toward red and
                    nobody can see it by looking.

A skew that is the same everywhere is survivable: measure it, subtract it, say
so on the methodology page. A skew that differs by paper is not, because then
the instrument is being unfair to particular titles — which is the accusation
this whole project will face on day one.

So the number that matters is not the average error. It is how much the
average error varies between papers.
"""

from collections import defaultdict


def _stats(pairs):
    if not pairs:
        return {"n": 0}
    errs = [m - g for m, g in pairs]
    n = len(errs)
    bias = sum(errs) / n
    mae = sum(abs(e) for e in errs) / n
    var = sum((e - bias) ** 2 for e in errs) / n
    within1 = sum(1 for e in errs if abs(e) <= 1) / n
    sign = sum(1 for m, g in pairs
               if (m > 0) == (g > 0) and (m < 0) == (g < 0)) / n
    return {"n": n, "bias": round(bias, 3), "mae": round(mae, 3),
            "sd": round(var ** 0.5, 3), "within_1": round(within1, 3),
            "same_sign": round(sign, 3)}


def calibrate(model, gold, groups=None, min_group=20):
    """model/gold: {item_id: score or None}
    groups: {item_id: label} — outlet, desk, whatever you want checked.

    Returns overall figures, per-group figures, and a verdict on whether the
    skew is constant enough to subtract.
    """
    ids = [i for i in sorted(set(model) & set(gold))
           if model[i] is not None and gold[i] is not None]
    pairs = [(model[i], gold[i]) for i in ids]
    out = {"overall": _stats(pairs), "groups": {}, "abstain": {}}

    ga = sum(1 for i in set(model) & set(gold) if model[i] is None)
    gg = sum(1 for i in set(model) & set(gold) if gold[i] is None)
    out["abstain"] = {"model_only": ga, "gold_only": gg}

    if groups:
        by = defaultdict(list)
        for i in ids:
            by[groups.get(i, "_none")].append((model[i], gold[i]))
        for k, v in sorted(by.items()):
            out["groups"][k] = _stats(v)

        big = {k: v for k, v in out["groups"].items() if v["n"] >= min_group}
        if len(big) >= 2:
            biases = [v["bias"] for v in big.values()]
            spread = max(biases) - min(biases)
            out["skew"] = {
                "overall": out["overall"]["bias"],
                "spread_between_groups": round(spread, 3),
                "worst_low": min(big, key=lambda k: big[k]["bias"]),
                "worst_high": max(big, key=lambda k: big[k]["bias"]),
                "constant_enough_to_subtract": spread < 0.25,
                "groups_measured": len(big),
            }
        else:
            out["skew"] = {"groups_measured": len(big),
                           "note": f"need >= {min_group} items in at least two "
                                   f"groups before per-group skew means anything"}
    return out


def report(c):
    o = c["overall"]
    if not o["n"]:
        return "nothing to calibrate"
    lines = [
        f"{o['n']} scored items compared with the gold set",
        f"  skew (model minus human)  {o['bias']:+.2f}",
        f"  average error             {o['mae']:.2f}",
        f"  spread of error           {o['sd']:.2f}",
        f"  within 1 point            {o['within_1']:.0%}",
        f"  same side of zero         {o['same_sign']:.0%}",
    ]
    if c.get("groups"):
        lines.append("\n  by group:")
        for k, v in c["groups"].items():
            if v["n"]:
                lines.append(f"    {k:<14} n={v['n']:<5} skew {v['bias']:+.2f}  "
                             f"error {v['mae']:.2f}")
    s = c.get("skew") or {}
    if "note" in s:
        # say so out loud: a missing verdict reads as "nothing wrong found"
        lines.append("")
        lines.append(f"  Cannot judge fairness between papers yet - {s['note']}.")
        lines.append("  Label more before trusting the per-group numbers above.")
    if "constant_enough_to_subtract" in s:
        lines.append("")
        if s["constant_enough_to_subtract"]:
            lines.append(
                f"  The skew is close to the same for every group "
                f"(spread {s['spread_between_groups']:.2f}).")
            lines.append(
                f"  Subtract {s['overall']:+.2f} from every score and say so on "
                f"the methodology page.")
        else:
            lines.append(
                f"  The skew is NOT constant: it varies by "
                f"{s['spread_between_groups']:.2f} between groups, worst at "
                f"'{s['worst_low']}' and '{s['worst_high']}'.")
            lines.append(
                "  Do not subtract a single offset. This is the instrument "
                "being unfair to particular titles, and it has to be fixed in "
                "the rubric or the prompt before launch.")
    return "\n".join(lines)


def offset_stability(by_slice):
    """The plan's test for headline-only scoring: score some items on the
    headline and again on the full text, and see whether the gap is a stable
    constant or moves around.

    by_slice: {label: [(headline_score, fulltext_score)]}
    """
    out = {}
    for k, pairs in by_slice.items():
        out[k] = _stats(pairs)
    big = {k: v for k, v in out.items() if v.get("n", 0) >= 20}
    if len(big) >= 2:
        b = [v["bias"] for v in big.values()]
        out["_verdict"] = {
            "spread": round(max(b) - min(b), 3),
            "stable": (max(b) - min(b)) < 0.25,
            "mean_offset": round(sum(b) / len(b), 3),
        }
    return out
