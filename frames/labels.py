"""Putting the words inside the shapes.

Three things have to be true at once, in this order, and the order is the
whole difficulty:

  1. the label sits inside its own shape
  2. it does not touch any other label
  3. given those, it sits as near the middle of its shape as it can

Centring text and hoping is what the first renderer did, and it produced
labels hanging over their neighbours. The fix has three parts, all of which
the prototype worked out and this is a port of.

**Where to put it.** The deepest point in the shape - the point furthest from
any edge - not the centre of gravity, which for a crescent-shaped cell can sit
outside the cell entirely. Found with a distance transform over a coarse grid.

**How to break the lines.** Not to a rectangle. A rectangle inside an irregular
cell has to be small enough for its narrowest row, which wastes most of the
shape. Instead each line is measured against the width the shape actually has
at that line's height, so the text takes the shape of what it is sitting in.

**What to do when it does not fit.** Shrink and try again, then try the next
anchor, and if nothing fits leave the shape unlabelled. An unlabelled shape is
a shape you can hover; an overlapping label is two shapes you cannot read.
"""

import math

from frames.fit import row_spans

GRID = 150          # for finding the deepest point in each shape
LINE_H = 1.12       # leading, as a multiple of font size
PAD = 3.0           # clear space demanded between two labels, in px


# Widths for a bold sans face, as a fraction of font size, calibrated against
# the real thing: bold Helvetica at 20px measured in a browser, over the
# headlines this actually has to draw. Within 3%.
#
# It has to be right rather than roughly right. The same function decides both
# how many words fit on a line and whether two labels collide, so an estimate
# that runs low produces labels that pass the collision check and then overlap
# when drawn.
_NARROW = set("iljtIfr.,:;'!|()[]{}")
_WIDE = set("mwMW@%")


def text_width(s, fs):
    w = 0.0
    for ch in s:
        if ch in _NARROW:
            w += 0.315
        elif ch in _WIDE:
            w += 0.965
        elif ch.isupper() or ch.isdigit():
            w += 0.651
        elif ch == " ":
            w += 0.284
        else:
            w += 0.567
    return w * fs


def anchors(layout, n_cells, grid=GRID, per_cell=3):
    """The deepest points inside each shape, best first.

    A distance transform over a grid: how far is this point from the nearest
    point belonging to somebody else, or from the rim.
    """
    sx, sy, w = layout["sx"], layout["sy"], layout["w"]
    own = [[-1] * grid for _ in range(grid)]
    for gy in range(grid):
        y = -1.0 + (gy + 0.5) * 2.0 / grid
        if y * y >= 1.0:
            continue
        for cell, a, b in row_spans(sx, sy, w, y):
            x0 = max(0, int((a + 1.0) / 2.0 * grid))
            x1 = min(grid - 1, int((b + 1.0) / 2.0 * grid))
            for gx in range(x0, x1 + 1):
                own[gy][gx] = cell

    BIG = 1e9
    dist = [[0.0 if own[gy][gx] < 0 else BIG for gx in range(grid)]
            for gy in range(grid)]
    # two passes of a chamfer transform: forward then backward
    for gy in range(grid):
        for gx in range(grid):
            if dist[gy][gx] == 0.0:
                continue
            c = own[gy][gx]
            best = dist[gy][gx]
            for dy, dx, cost in ((-1, 0, 1.0), (0, -1, 1.0),
                                 (-1, -1, 1.414), (-1, 1, 1.414)):
                ny, nx = gy + dy, gx + dx
                if 0 <= ny < grid and 0 <= nx < grid:
                    d = dist[ny][nx] if own[ny][nx] == c else 0.0
                    best = min(best, d + cost)
                else:
                    best = min(best, cost)
            dist[gy][gx] = best
    for gy in range(grid - 1, -1, -1):
        for gx in range(grid - 1, -1, -1):
            if own[gy][gx] < 0:
                continue
            c = own[gy][gx]
            best = dist[gy][gx]
            for dy, dx, cost in ((1, 0, 1.0), (0, 1, 1.0),
                                 (1, 1, 1.414), (1, -1, 1.414)):
                ny, nx = gy + dy, gx + dx
                if 0 <= ny < grid and 0 <= nx < grid:
                    d = dist[ny][nx] if own[ny][nx] == c else 0.0
                    best = min(best, d + cost)
                else:
                    best = min(best, cost)
            dist[gy][gx] = best

    found = [[] for _ in range(n_cells)]
    for gy in range(grid):
        for gx in range(grid):
            c = own[gy][gx]
            if c >= 0:
                found[c].append((dist[gy][gx],
                                 -1.0 + (gx + 0.5) * 2.0 / grid,
                                 -1.0 + (gy + 0.5) * 2.0 / grid))
    out = []
    for c in range(n_cells):
        pts = sorted(found[c], reverse=True)
        keep = []
        for d, x, y in pts:
            # spread the alternates out, or they are all the same spot
            if all(math.hypot(x - kx, y - ky) > 0.10 for _, kx, ky in keep):
                keep.append((d, x, y))
            if len(keep) >= per_cell:
                break
        out.append(keep)
    return out


def cell_rows(layout, cell, rows):
    """{row y: (x0, x1)} for one shape."""
    sx, sy, w = layout["sx"], layout["sy"], layout["w"]
    out = {}
    for r in range(rows):
        y = -1.0 + (r + 0.5) * 2.0 / rows
        for c, a, b in row_spans(sx, sy, w, y):
            if c == cell:
                out[r] = (a, b)
    return out


def fit_lines(spans, rows, ax, ay, fs, words, R, tail=None):
    """Pack words into whatever width the shape has at each line's height.

    Returns [(text, centre_x, y)] in disc coordinates, or None.
    """
    line_px = fs * LINE_H
    line_u = line_px / R * 2.0 / 2.0          # px -> disc units (R px = 1 unit)
    line_u = line_px / R
    extra = 1 if tail else 0

    for n_lines in range(1, 6):
        total = (n_lines + extra) * line_u
        top = ay + total / 2.0 - line_u / 2.0
        out, wi, ok = [], 0, True
        for k in range(n_lines):
            y = top - k * line_u
            r = int((y + 1.0) / 2.0 * rows)
            span = spans.get(r)
            if not span:
                ok = False
                break
            avail = (span[1] - span[0]) * 0.92 * R
            if avail < fs * 2.2:
                ok = False
                break
            line = ""
            while wi < len(words):
                cand = (line + " " + words[wi]).strip()
                if line and text_width(cand, fs) > avail:
                    break
                line = cand
                wi += 1
            if not line:
                ok = False
                break
            # The line takes its WIDTH from the shape at this height, but all
            # the lines of one label share a centre. Letting each line sit on
            # its own row's midpoint makes the block stagger diagonally on any
            # shape near the rim - measured at 60px of drift over five lines,
            # which both looks wrong and swells the label's footprint until it
            # collides with its neighbour.
            half = text_width(line, fs) / 2.0 / R
            lo_x, hi_x = span[0] + half, span[1] - half
            if lo_x > hi_x:
                ok = False
                break
            out.append((line, min(hi_x, max(lo_x, ax)), y))
        if not ok or wi < len(words):
            continue
        if tail:
            y = top - n_lines * line_u
            r = int((y + 1.0) / 2.0 * rows)
            span = spans.get(r)
            th = text_width(tail, fs * 0.66) / 2.0 / R
            if not span or span[0] + th > span[1] - th:
                continue
            out.append((tail, min(span[1] - th, max(span[0] + th, ax)), y))
        return out
    return None


def place(stories, layout, R, rows=260, band=(9.0, 22.0), boost=4.0):
    """Lay out every label. Biggest shapes first, so they get the good spots.

    Returns [{cell, lines, fs, boxes}] for the ones that fit.
    """
    n = len(stories)
    anc = anchors(layout, n)
    order = sorted(range(n), key=lambda i: -stories[i]["area"])
    total_area = sum(s["area"] for s in stories) or 1.0

    placed, out = [], []
    for i in order:
        s = stories[i]
        if not anc[i]:
            continue
        deep = anc[i][0][0]                       # grid units of depth
        share = s["area"] / total_area
        # type scales with the shape, sub-linearly, with a lift for the biggest
        t = min(1.0, deep / 22.0)
        fs0 = band[0] + (band[1] - band[0]) * (t ** 0.72) + boost * max(0.0, t - 0.72) / 0.28
        spans = cell_rows(layout, i, rows)
        if not spans:
            continue
        words = s["name"].split()
        tail = None if s.get("stance") is None else f"{s['stance']:+.1f}"

        hit = None
        for a_i, (_, ax, ay) in enumerate(anc[i]):
            for k in range(11):
                fs = fs0 * (0.9 ** k)
                if fs < band[0] * 0.8:
                    break
                lines = fit_lines(spans, rows, ax, ay, fs, words, R, tail)
                if not lines:
                    continue
                boxes = []
                for txt, cx, y in lines:
                    f = fs * 0.66 if txt == tail else fs
                    boxes.append((cx * R, -y * R, text_width(txt, f) / 2.0, f * 0.62))
                if any(_hits(b, p) for b in boxes for p in placed):
                    continue
                hit = (lines, fs, boxes)
                break
            if hit:
                break
        if not hit:
            continue
        lines, fs, boxes = hit
        placed.extend(boxes)
        out.append({"cell": i, "lines": lines, "fs": fs, "tail": tail})
    return out


def _hits(a, b):
    return (abs(a[0] - b[0]) < a[2] + b[2] + PAD
            and abs(a[1] - b[1]) < a[3] + b[3] + PAD)
