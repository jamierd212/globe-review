"""Drawing the globe.

Straight from the frame, so the picture is the numbers rather than an
illustration of them. Output is a single SVG file that needs nothing to open.

Every shape's outline comes from the same row spans the area fitting uses: for
each horizontal line, where the shape starts and stops. Walk down the left
edge and back up the right and you have the outline exactly, with no tracing
or smoothing.

Three encodings, the same ones throughout:

  area    the story's share of coverage
  colour  how the press is framing it, red to green, graded across the shape
          from what the left-leaning papers make of it to the right-leaning
  place   direction is the centre of gravity of the papers running it;
          distance from the middle is how widely it is carried
"""

import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ROWS = 300
SIZE = 900
PAD = 26

# the sphere's red-to-green scale
STOPS = [(224, 30, 30), (245, 100, 100), (230, 226, 212), (82, 201, 122), (15, 143, 66)]
PAPER = (251, 252, 251)


def tone(s):
    if s is None:
        return (188, 190, 186)
    t = max(-2.0, min(2.0, s)) + 2.0
    lo = min(int(t), 3)
    f = t - lo
    a, b = STOPS[lo], STOPS[lo + 1]
    return tuple(a[i] + (b[i] - a[i]) * f for i in range(3))


def rgb(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in c)


def outlines(layout, rows=ROWS):
    """One polygon per shape, taken from the row spans."""
    from frames.fit import row_spans
    sx, sy, w = layout["sx"], layout["sy"], layout["w"]
    n = len(sx)
    left = [[] for _ in range(n)]
    right = [[] for _ in range(n)]
    h = 2.0 / rows
    for r in range(rows):
        y = -1.0 + (r + 0.5) * h
        for cell, a, b in row_spans(sx, sy, w, y):
            left[cell].append((a, y))
            right[cell].append((b, y))
    return [l + list(reversed(r)) for l, r in zip(left, right)]


def write(frame, path=None, size=SIZE):
    stories = frame["stories"]
    layout = frame["layout"]
    polys = outlines(layout)
    R = (size - 2 * PAD) / 2.0
    cx = cy = size / 2.0

    def px(x, y):
        return (cx + x * R, cy - y * R)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" font-family="Helvetica Neue, Arial, sans-serif">',
        f'<rect width="{size}" height="{size}" fill="#eef1f0"/>',
        "<defs>",
    ]

    # one left-to-right gradient per shape: the left end is what the
    # left-leaning press makes of it, the right end the right-leaning press
    for i, s in enumerate(stories):
        lo, hi = s.get("stance_lo"), s.get("stance_hi")
        if lo is None:
            lo = hi = s.get("stance")
        parts.append(
            f'<linearGradient id="g{i}" x1="0" y1="0" x2="1" y2="0">'
            f'<stop offset="0" stop-color="{rgb(tone(lo))}"/>'
            f'<stop offset="1" stop-color="{rgb(tone(hi))}"/></linearGradient>')
    # the lighting: a highlight up and to the left, shadow to the lower right.
    # Purely cosmetic - the cells are laid out flat so no area is distorted.
    parts.append(
        '<radialGradient id="lit" cx="0.34" cy="0.28" r="0.78">'
        '<stop offset="0" stop-color="#ffffff" stop-opacity="0.42"/>'
        '<stop offset="0.55" stop-color="#ffffff" stop-opacity="0.05"/>'
        '<stop offset="1" stop-color="#1a1608" stop-opacity="0.42"/>'
        '</radialGradient>'
        '<radialGradient id="rim" cx="0.5" cy="0.5" r="0.5">'
        '<stop offset="0.86" stop-color="#000000" stop-opacity="0"/>'
        '<stop offset="1" stop-color="#000000" stop-opacity="0.30"/>'
        '</radialGradient>'
        f'<clipPath id="disc"><circle cx="{cx}" cy="{cy}" r="{R}"/></clipPath>'
        "</defs>",
    )
    parts.append(f'<g clip-path="url(#disc)">')

    for i, s in enumerate(stories):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in
                       (px(a, b) for a, b in polys[i]))
        parts.append(f'<polygon points="{pts}" fill="url(#g{i})" '
                     f'stroke="{rgb(PAPER)}" stroke-width="1.1" '
                     f'stroke-linejoin="round"/>')

    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="url(#lit)"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy}" r="{R}" fill="url(#rim)"/>')

    # labels: at the widest point of each shape, biggest shapes first, and
    # only where the shape is big enough to hold the words
    placed = []
    for i, s in enumerate(stories):
        poly = polys[i]
        if len(poly) < 4:
            continue
        half = len(poly) // 2
        best, bw = None, 0
        for k in range(half):
            a, y = poly[k]
            b, _ = poly[len(poly) - 1 - k]
            if b - a > bw:
                bw, best = b - a, ((a + b) / 2, y)
        if not best:
            continue
        wpx = bw * R
        area = s["area"] / sum(t["area"] for t in stories)
        fs = max(9.0, min(21.0, 7 + 46 * math.sqrt(area)))
        words = s["name"].split()
        per = max(1, int(wpx / (fs * 0.52)))
        lines, cur = [], ""
        for wd in words:
            if len(cur) + len(wd) + 1 <= per:
                cur = (cur + " " + wd).strip()
            else:
                if cur:
                    lines.append(cur)
                cur = wd
            if len(lines) >= 3:
                break
        if cur and len(lines) < 3:
            lines.append(cur)
        if not lines or wpx < 46:
            continue
        x, y = px(*best)
        hh = len(lines) * fs * 1.12
        if any(abs(x - p[0]) < (bw * R * 0.5 + p[2]) and abs(y - p[1]) < (hh / 2 + p[3])
               for p in placed):
            continue
        placed.append((x, y, bw * R * 0.5, hh / 2))
        st = s.get("stance")
        ink = "#fbf9f0" if (st is None or st < -0.35 or st > 0.9) else "#16180f"
        sh = "0 1px 2px rgba(0,0,0,.45)"
        parts.append(f'<g text-anchor="middle" fill="{ink}" '
                     f'style="paint-order:stroke;text-shadow:{sh}">')
        top = y - hh / 2 + fs * 0.9
        for k, ln in enumerate(lines):
            parts.append(f'<text x="{x:.1f}" y="{top + k * fs * 1.12:.1f}" '
                         f'font-size="{fs:.1f}" font-weight="700">{esc(ln)}</text>')
        if s.get("stance") is not None:
            parts.append(f'<text x="{x:.1f}" y="{top + len(lines) * fs * 1.12 + 1:.1f}" '
                         f'font-size="{fs * 0.62:.1f}" font-weight="500" '
                         f'font-family="monospace" opacity="0.92">'
                         f'{s["stance"]:+.1f}</text>')
        parts.append("</g>")

    parts.append("</g>")
    n_scored = sum(1 for s in stories if s.get("stance") is not None)
    parts.append(
        f'<text x="{PAD}" y="{PAD}" font-size="13" fill="#4b5654" font-weight="600">'
        f'Front Page Monitor &#183; {len(stories)} stories, '
        f'{sum(s["articles"] for s in stories)} articles</text>'
        f'<text x="{PAD}" y="{PAD + 17}" font-size="11" fill="#7c8785">'
        f'{frame["generated"][:16].replace("T", " ")} UTC &#183; last '
        f'{frame["window_hours"]}h &#183; {n_scored} of {len(stories)} scored'
        f'</text>')
    parts.append("</svg>")

    path = path or os.path.join(ROOT, "data", "globe.svg")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


def esc(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
