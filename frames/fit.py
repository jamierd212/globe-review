"""Working out the shapes on the globe.

Each story gets a share of the disc equal to its share of coverage. The
shapes are not slices from a centre — they push against each other, so a story
can only grow by taking room from another, which is what a news cycle actually
does.

How it works: every story has a seed point and a weight. A point on the disc
belongs to whichever story has the smallest (distance squared minus weight).
Raising a story's weight makes its shape grow. So the job is to find the
weights that give every story the area it should have, which is done by
nudging them and re-measuring until they fit.

The prototype measured areas by sprinkling points over the disc and counting.
That needs a lot of points to be accurate and was far too slow without numpy.
This does it differently: along any horizontal line across the disc, the
shapes are just intervals, and where each interval starts and stops can be
worked out directly. Sum the interval lengths and you have the exact area of
every shape, with no sampling error at all — about a hundred times less work
than counting points, and more accurate.

Two entry points:

    fit    from scratch. Used for the first frame, and when jumping a long way
           back through the archive.
    refit  from yesterday's answer. Used for every ordinary frame: it is much
           faster, and it keeps each shape where it was, which is what makes
           playback flow instead of boil.
"""

import math

# Measured on a 40-shape layout: 300 rows / 90 rounds gives 0.6% worst area
# error in 0.7s; 420 / 160 gives 0.2% in 1.6s. An hourly job can afford either,
# so this errs toward accuracy. Drop them for tests.
ROWS = 420          # horizontal lines across the disc. Areas are exact along
                    # each line, so this only limits vertical resolution.
RELAX = 520         # how long to spend spacing the seeds out
ITERS = 160         # how long to spend getting the areas right
BAND = 0.14         # how far a seed may drift from its ring


# --------------------------------------------------------------- geometry ---

def row_spans(sx, sy, w, y):
    """Which shape owns which stretch of the horizontal line at height y.

    Along that line, shape i's score is (x-sx)^2 + (y-sy)^2 - w, and the winner
    is whoever scores lowest. Dropping the x^2 that every shape shares, each
    one becomes a straight line in x, and the winner at any point is whichever
    line is lowest. The lower edge of a set of straight lines is a simple
    shape to compute, which is what makes this fast and exact.

    Returns [(cell, x_from, x_to)] left to right.
    """
    if y * y >= 1.0:
        return []
    xr = math.sqrt(1.0 - y * y)
    n = len(sx)

    # each shape as a straight line: slope, height, and who it belongs to
    lines = []
    for i in range(n):
        dy = y - sy[i]
        lines.append((-2.0 * sx[i], sx[i] * sx[i] + dy * dy - w[i], i))
    # steepest first, so the crossing points come out in left-to-right order
    lines.sort(key=lambda t: (-t[0], t[1]))

    hull = []

    def cross(a, b):
        return (b[1] - a[1]) / (a[0] - b[0])

    for L in lines:
        if hull and hull[-1][0] == L[0]:
            if hull[-1][1] <= L[1]:
                continue                      # same slope, already beaten
            hull.pop()
        while len(hull) >= 2 and cross(hull[-2], L) <= cross(hull[-2], hull[-1]):
            hull.pop()
        if len(hull) == 1 and cross(hull[-1], L) <= -1e18:
            hull.pop()
        hull.append(L)

    spans, left = [], -xr
    for k in range(len(hull)):
        right = xr if k == len(hull) - 1 else min(xr, cross(hull[k], hull[k + 1]))
        if right > left:
            spans.append((hull[k][2], left, right))
            left = right
        if left >= xr:
            break
    return spans


def areas(sx, sy, w, rows=ROWS):
    """Each shape's share of the disc. Sums to 1."""
    n = len(sx)
    out = [0.0] * n
    h = 2.0 / rows
    total = 0.0
    for r in range(rows):
        y = -1.0 + (r + 0.5) * h
        for cell, a, b in row_spans(sx, sy, w, y):
            out[cell] += (b - a) * h
            total += (b - a) * h
    if total > 0:
        out = [a / total for a in out]
    return out


# ------------------------------------------------------------------- fit ----

def _anchors(items):
    """Where each shape starts out: direction from which papers are covering
    it, distance from the centre by how widely it is covered."""
    n = len(items)
    order = sorted(range(n), key=lambda i: items[i]["rank_key"])
    ring = [0.0] * n
    for place, i in enumerate(order):
        ring[i] = 0.09 + 0.83 * (place / (n - 1) if n > 1 else 0)
    return ring


def _relax(sx, sy, ring, ang, rad, passes, band=BAND):
    """Push overlapping seeds apart, but let them slide around their ring
    rather than away from it — the distance from the centre is carrying
    meaning, so it is the angle that should absorb the crowding."""
    n = len(sx)
    for _ in range(passes):
        for a in range(n):
            for b in range(a + 1, n):
                dx, dy = sx[b] - sx[a], sy[b] - sy[a]
                d = math.hypot(dx, dy) or 1e-6
                want = (rad[a] + rad[b]) * 1.05
                if d < want:
                    push = ((want - d) / 2) * 0.45 / d
                    sx[a] -= dx * push; sy[a] -= dy * push
                    sx[b] += dx * push; sy[b] += dy * push
        for c in range(n):
            r = math.hypot(sx[c], sy[c]) or 1e-6
            th = math.atan2(sy[c], sx[c])
            da = (th - ang[c] + math.pi * 3) % (math.pi * 2) - math.pi
            th = ang[c] + da * 0.92
            rr = min(ring[c] + band, max(ring[c] - band, r))
            sx[c] = rr * math.cos(th); sy[c] = rr * math.sin(th)


# A shape squeezed out completely cannot recover on its own, because the nudge
# it gets is proportional to the area it is missing and it has none. So it
# needs a shove — but only if it is genuinely stuck. Shapes routinely pass
# through zero on the way to their proper size, and shoving those makes the
# whole fit oscillate: measured across four layouts, shoving on sight took the
# worst error from 0.6% to 18%. Waiting a few rounds first costs nothing when
# nothing is stuck, and rescues the case where something is.
STUCK_ROUNDS = 3
STUCK_PUSH = 8.0


def _fit_weights(sx, sy, w, target, iters, rows, lr0=0.85):
    n = len(sx)
    stuck = [0] * n
    for t in range(iters):
        got = areas(sx, sy, w, rows)
        lr = lr0 * (0.968 ** t) + 0.14
        mean = 0.0
        for i in range(n):
            step = lr * (target[i] - got[i]) * 2.4
            stuck[i] = stuck[i] + 1 if got[i] <= 0.0 else 0
            if stuck[i] >= STUCK_ROUNDS:
                step += lr * target[i] * STUCK_PUSH
            w[i] = max(-1.6, min(1.6, w[i] + step))
            mean += w[i]
        mean /= n
        for i in range(n):
            w[i] -= mean
    return w


def fit(items, rows=ROWS, relax=RELAX, iters=ITERS):
    """items: [{id, angle, rank_key, area}] -> layout dict.

    `area` is the raw share of coverage; it is normalised here.
    """
    n = len(items)
    if n == 0:
        return {"ids": [], "sx": [], "sy": [], "w": [], "n": 0}
    tot = sum(i["area"] for i in items) or 1.0
    target = [i["area"] / tot for i in items]
    ang = [i["angle"] for i in items]
    ring = _anchors(items)
    rad = [math.sqrt(t) for t in target]

    sx = [ring[i] * math.cos(ang[i]) for i in range(n)]
    sy = [ring[i] * math.sin(ang[i]) for i in range(n)]
    _relax(sx, sy, ring, ang, rad, relax)
    w = _fit_weights(sx, sy, [0.0] * n, target, iters, rows)
    return {"ids": [i["id"] for i in items], "sx": sx, "sy": sy, "w": w,
            "n": n, "target": target}


def refit(prev, items, rows=ROWS, relax=24, iters=14):
    """Start from the previous frame's answer.

    Shapes are matched by story id, not by position in a list, because stories
    arrive and leave. A story that has just appeared starts at its own anchor
    with the smallest weight in play, so it grows out of the gutter rather
    than appearing full size.
    """
    n = len(items)
    if n == 0:
        return {"ids": [], "sx": [], "sy": [], "w": [], "n": 0}
    tot = sum(i["area"] for i in items) or 1.0
    target = [i["area"] / tot for i in items]
    ang = [i["angle"] for i in items]
    ring = _anchors(items)
    rad = [math.sqrt(t) for t in target]

    where = {sid: k for k, sid in enumerate(prev.get("ids", []))}
    min_w = min(prev.get("w") or [0.0])
    sx, sy, w = [], [], []
    for i, it in enumerate(items):
        k = where.get(it["id"])
        if k is None:
            sx.append(ring[i] * math.cos(ang[i]))
            sy.append(ring[i] * math.sin(ang[i]))
            w.append(min_w - 0.05)
        else:
            sx.append(prev["sx"][k]); sy.append(prev["sy"][k]); w.append(prev["w"][k])

    _relax(sx, sy, ring, ang, rad, relax)
    w = _fit_weights(sx, sy, w, target, iters, rows, lr0=0.30)
    return {"ids": [i["id"] for i in items], "sx": sx, "sy": sy, "w": w,
            "n": n, "target": target}


# ----------------------------------------------------------------- checks ---

def check(layout, rows=ROWS):
    """The three things that have to be true, and are cheap to keep true."""
    got = areas(layout["sx"], layout["sy"], layout["w"], rows)
    target = layout.get("target") or got
    errs = [abs(g - t) / t if t > 0 else 0.0 for g, t in zip(got, target)]
    empty = [layout["ids"][i] for i, g in enumerate(got) if g <= 1e-9]
    return {
        "worst_area_error": round(max(errs) if errs else 0, 4),
        "mean_area_error": round(sum(errs) / len(errs) if errs else 0, 4),
        "empty_shapes": empty,
        "areas": got,
    }


def radial_order(layout, rank_keys):
    """Does distance from the centre still mean what it is supposed to?

    Returns Spearman's rank correlation between how widely a story is covered
    and how far out it actually landed. Should be strongly positive; if it
    goes near zero the position is decorative and the globe is lying.
    """
    n = layout["n"]
    if n < 3:
        return None
    dist = [math.hypot(layout["sx"][i], layout["sy"][i]) for i in range(n)]

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for place, i in enumerate(order):
            r[i] = place
        return r

    a, b = ranks(rank_keys), ranks(dist)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return round(num / den, 3) if den else None
