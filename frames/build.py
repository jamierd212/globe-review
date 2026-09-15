"""Turn the scored database into one frame, and draw it.

    python3 -m frames.build            write data/frame.json and data/globe.svg
    python3 -m frames.build --hours 48

A frame is everything needed to draw the globe at one moment, and nothing
else: per story, its share of coverage, where it sits, how the press is
framing it, and the fitted geometry. The geometry is in the frame because it
cannot be recovered afterwards - warm-starting tomorrow from today's answer is
what makes playback flow instead of boil.

The picture is drawn straight from the same numbers, so what you see is the
frame rather than an illustration of it.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ingest import db                      # noqa: E402
from frames import fit as F                # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WINDOW = 48

# How many shapes the globe carries. Not a rendering choice - a reading one.
# The first real frame drew 127 and was unreadable: shapes too small to label,
# and the eye has nowhere to start. Everything below the cut is still collected
# and still scored, it simply is not the front page.
TOP = 40


def collect(con, hours=WINDOW, min_outlets=2, top=TOP):
    cut = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat(
        timespec="seconds")
    press = {r["id"]: (r["leaning"], r["market"], r["weight"])
             for r in con.execute("SELECT id, leaning, market, weight FROM outlet")}

    rows = [dict(r) for r in con.execute(
        "SELECT s.id, s.name, s.issue_id, s.target, a.outlet_id, "
        "       sc.stance, sc.confidence, sc.desk, sc.quote, a.title "
        "FROM story s "
        "JOIN story_member m ON m.story_id = s.id "
        "JOIN article a ON a.id = m.article_id "
        "LEFT JOIN article_score sc ON sc.article_id = a.id "
        "WHERE s.status = 'live' AND s.last_seen >= ?", (cut,))]

    stories = {}
    for r in rows:
        st = stories.setdefault(r["id"], {
            "id": r["id"], "name": r["name"], "issue_id": r["issue_id"],
            "target": r["target"], "outlets": {}, "articles": 0, "scored": 0})
        st["articles"] += 1
        o = st["outlets"].setdefault(r["outlet_id"], {"n": 0, "scores": []})
        o["n"] += 1
        if r["stance"] is not None:
            # a low-confidence score counts, but counts for less
            w = {"high": 1.0, "medium": 0.7, "low": 0.35}.get(r["confidence"], 0.7)
            o["scores"].append((r["stance"], w))
            st["scored"] += 1

    out = []
    for st in stories.values():
        if len(st["outlets"]) < min_outlets:
            continue
        # where it sits: the centre of gravity of the papers running it
        x = y = wsum = 0.0
        for oid, o in st["outlets"].items():
            if oid not in press:
                continue
            lean, mkt, wt = press[oid]
            x += lean * wt; y += mkt * wt; wsum += wt
        if not wsum:
            continue
        angle = math.atan2(y / wsum, x / wsum)

        # colour: fit stance against where each paper sits left to right, so a
        # shape that sweeps from red to green IS the press disagreeing
        pts = []
        for oid, o in st["outlets"].items():
            if oid in press and o["scores"]:
                sw = sum(w for _, w in o["scores"]) or 1
                mean = sum(s * w for s, w in o["scores"]) / sw
                pts.append((press[oid][0], mean, sw))
        lo = hi = mean_all = None
        if pts:
            sw = sum(p[2] for p in pts)
            mean_all = sum(p[1] * p[2] for p in pts) / sw
            if len(pts) > 1:
                mx = sum(p[0] * p[2] for p in pts) / sw
                sxx = sum(p[2] * (p[0] - mx) ** 2 for p in pts)
                sxy = sum(p[2] * (p[0] - mx) * (p[1] - mean_all) for p in pts)
                slope = sxy / sxx if sxx > 1e-9 else 0.0
                xs = [p[0] for p in pts]
                lo = mean_all + slope * (min(xs) - mx)
                hi = mean_all + slope * (max(xs) - mx)
                # ease the ends back so one outlier does not set the whole shape
                lo = mean_all + (lo - mean_all) * 0.88
                hi = mean_all + (hi - mean_all) * 0.88
            else:
                lo = hi = mean_all

        out.append({
            "id": st["id"], "name": st["name"], "issue_id": st["issue_id"],
            "target": st["target"],
            "articles": st["articles"], "scored": st["scored"],
            "outlets": sorted(st["outlets"]),
            "angle": angle,
            "rank_key": -len(st["outlets"]),      # breadth: broad = centre
            "area": float(st["articles"]),
            "stance": mean_all, "stance_lo": lo, "stance_hi": hi,
        })
    out.sort(key=lambda s: -s["area"])
    return out[:top] if top else out


def build(hours=WINDOW, db_path=None, top=TOP):
    con = db.connect(db_path)
    stories = collect(con, hours, top=top)
    if not stories:
        print("no live stories with two or more outlets - run the clusterer")
        return None
    layout = F.fit(stories, rows=360, relax=420, iters=140)
    chk = F.check(layout, rows=360)
    rho = F.radial_order(layout, [s["rank_key"] for s in stories])

    frame = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_hours": hours,
        "stories": stories,
        "layout": {"ids": layout["ids"], "sx": layout["sx"],
                   "sy": layout["sy"], "w": layout["w"]},
        "checks": {"worst_area_error": chk["worst_area_error"],
                   "empty_shapes": chk["empty_shapes"],
                   "radial_order": rho},
    }
    path = os.path.join(ROOT, "data", "frame.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(frame, f)
    print(f"{len(stories)} stories, {sum(s['articles'] for s in stories)} articles")
    print(f"  worst area error {chk['worst_area_error']:.1%}, "
          f"empty shapes {len(chk['empty_shapes'])}, radial order {rho}")
    print(f"  wrote {path}")
    con.close()
    return frame


if __name__ == "__main__":
    a = sys.argv[1:]
    h = int(a[a.index("--hours") + 1]) if "--hours" in a else WINDOW
    t = int(a[a.index("--top") + 1]) if "--top" in a else TOP
    fr = build(hours=h, top=t)
    if fr:
        from frames import draw
        p = draw.write(fr)
        print(f"  wrote {p}")
