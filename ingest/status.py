"""Is everything still coming in?

The failure this exists to catch is the quiet one. A feed that stops answering
looks exactly like a paper having a slow news day: the globe carries on being
drawn, confidently, with a hole in it. Nothing throws an error. Nobody notices
for a week.

So the question is never "did the last run crash" — it is "has every source
produced what it normally produces, recently". Three things are checked:

  live      answered within the last few hours
  flowing   produced new articles, not just the same ones again
  normal    producing roughly what it usually does, not a tenth of it

A feed can be up, returning 200, parsing fine, and still be broken — a paper
that changes its feed URL often leaves the old one serving a frozen copy
forever. That is what `flowing` is for.

    python3 -m ingest.status            table in the terminal
    python3 -m ingest.status --html     also writes data/status.html
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta

from . import db, newsy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STALE_HOURS = 3          # no successful poll in this long = not live
FROZEN_HOURS = 24        # answering, but no new articles in this long
THIN = 0.35              # producing less than this share of normal = thin


def _hours_since(stamp, now):
    if not stamp:
        return None
    try:
        t = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return (now - t).total_seconds() / 3600.0


def collect(db_path=None):
    con = db.connect(db_path)
    now = datetime.now(timezone.utc)
    spec = json.load(open(os.path.join(ROOT, "outlets.json"), encoding="utf-8"))
    excluded = [(o["id"], o.get("excluded", "")) for o in spec["outlets"]
                if o.get("excluded")]
    rows = []
    for f in con.execute(
            "SELECT f.id, f.url, f.kind, f.outlet_id, o.name "
            "FROM feed f JOIN outlet o ON o.id = f.outlet_id ORDER BY o.name, f.kind"):
        polls = [dict(p) for p in con.execute(
            "SELECT polled_at, http_status, n_items, n_new, error FROM poll "
            "WHERE feed_id=? ORDER BY polled_at DESC LIMIT 60", (f["id"],))]
        ok = [p for p in polls if not p["error"] and p["n_items"] > 0]
        last_ok = ok[0]["polled_at"] if ok else None
        last_new = next((p["polled_at"] for p in polls if p["n_new"] > 0), None)

        # what this feed normally produces, from its own history
        counts = [p["n_items"] for p in ok]
        typical = sorted(counts)[len(counts) // 2] if counts else 0
        latest = ok[0]["n_items"] if ok else 0

        h_ok = _hours_since(last_ok, now)
        h_new = _hours_since(last_new, now)
        fails = 0
        for p in polls:
            if p["error"] or p["n_items"] == 0:
                fails += 1
            else:
                break

        # "Nothing new in 24 hours" only means something if we actually looked
        # several times in those 24 hours. Judging it on elapsed time instead
        # calls a feed frozen after any gap in polling - which is how this rule
        # first went wrong, flagging healthy feeds after a weekend off.
        looks = sum(1 for p in ok
                    if (_hours_since(p["polled_at"], now) or 1e9) <= FROZEN_HOURS)

        flags = []
        if h_ok is None or h_ok > STALE_HOURS:
            flags.append("NOT ANSWERING")
        elif typical and latest < typical * THIN:
            flags.append("THIN")
        if looks >= 3 and (h_new is None or h_new > FROZEN_HOURS):
            flags.append("FROZEN")

        rows.append({
            "outlet": f["name"], "outlet_id": f["outlet_id"], "kind": f["kind"],
            "url": f["url"], "last_ok": last_ok, "hours_since_ok": h_ok,
            "hours_since_new": h_new, "items": latest, "typical": typical,
            "consecutive_failures": fails, "polls": len(polls),
            "recent": [p["n_items"] for p in polls[:24]][::-1],
            "last_error": next((p["error"] for p in polls if p["error"]), None),
            "flags": flags,
        })

    day_ago = (now - timedelta(days=1)).isoformat(timespec="seconds")
    # counts are of NEWS. Sport and showbiz are collected but counted nowhere,
    # so that "share of a paper's own output" means the same thing for a title
    # that runs showbiz and one that does not.
    recent = [dict(r) for r in con.execute(
        "SELECT outlet_id, url_canon FROM article WHERE first_seen >= ?", (day_ago,))]
    news_recent, other_recent = newsy.split(recent)
    per_outlet = {}
    for r in news_recent:
        per_outlet[r["outlet_id"]] = per_outlet.get(r["outlet_id"], 0) + 1
    allrows = [dict(r) for r in con.execute("SELECT url_canon FROM article")]
    total = sum(1 for r in allrows if newsy.is_news(r["url_canon"]))
    excluded_total = len(allrows) - total
    con.close()
    return {"generated": now.isoformat(timespec="seconds"), "feeds": rows,
            "articles_total": total, "articles_24h": per_outlet,
            "not_news_24h": len(other_recent), "not_news_total": excluded_total,
            "excluded": excluded}


def text(s):
    bad = [r for r in s["feeds"] if r["flags"]]
    out = [f"Sources at {s['generated']}",
           f"{len(s['feeds'])} feeds, {sum(s['articles_24h'].values())} news "
           f"articles in the last 24h, {s['articles_total']} in total "
           f"({s['not_news_24h']} sport/showbiz set aside today)", ""]
    out.append(f"{'outlet':<14} {'kind':<8} {'last ok':>9} {'items':>6} "
               f"{'usual':>6} {'new':>9}  state")
    for r in sorted(s["feeds"], key=lambda r: (not r["flags"], r["outlet"])):
        ho = "never" if r["hours_since_ok"] is None else f"{r['hours_since_ok']:.1f}h"
        hn = "never" if r["hours_since_new"] is None else f"{r['hours_since_new']:.1f}h"
        state = ", ".join(r["flags"]) or "ok"
        out.append(f"{r['outlet'][:13]:<14} {r['kind']:<8} {ho:>9} {r['items']:>6} "
                   f"{r['typical']:>6} {hn:>9}  {state}")
    if bad:
        out += ["", f"{len(bad)} feed(s) need attention:"]
        for r in bad:
            out.append(f"  {r['outlet']} ({r['kind']}): {', '.join(r['flags'])}"
                       + (f" - {r['last_error']}" if r["last_error"] else ""))
            out.append(f"    {r['url']}")
    else:
        out += ["", "every feed answering, flowing and producing its usual amount."]
    if s["excluded"]:
        out += ["", "not collected:"]
        for oid, why in s["excluded"]:
            out.append(f"  {oid}: {why[:78]}")
    return "\n".join(out)


def html(s):
    def spark(vals, typical):
        if not vals:
            return ""
        top = max(max(vals), typical or 1)
        bars = "".join(
            f'<i style="height:{max(2, round(v / top * 22))}px"></i>' for v in vals)
        return f'<span class="spark">{bars}</span>'

    rows = []
    for r in sorted(s["feeds"], key=lambda r: (not r["flags"], r["outlet"])):
        cls = "bad" if any(f in ("NOT ANSWERING", "FROZEN") for f in r["flags"]) \
              else "warn" if r["flags"] else "ok"
        ho = "never" if r["hours_since_ok"] is None else f"{r['hours_since_ok']:.1f}h"
        rows.append(
            f'<tr class="{cls}"><td>{r["outlet"]}</td><td class="k">{r["kind"]}</td>'
            f'<td class="n">{ho}</td><td class="n">{r["items"]}</td>'
            f'<td class="n dim">{r["typical"]}</td>'
            f'<td>{spark(r["recent"], r["typical"])}</td>'
            f'<td class="s">{", ".join(r["flags"]) or "ok"}</td>'
            f'<td class="u">{r["url"]}</td></tr>')

    bad = [r for r in s["feeds"] if r["flags"]]
    banner = (f'<p class="alert">{len(bad)} feed(s) need attention</p>' if bad
              else '<p class="fine">every feed answering, flowing and normal</p>')
    return f"""<!doctype html><meta charset="utf-8">
<title>Front Page Monitor &mdash; sources</title>
<meta http-equiv="refresh" content="300">
<style>
 body{{font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
      background:#eef1f0;color:#101615;margin:0;padding:28px}}
 h1{{font-size:19px;margin:0 0 4px}}
 .meta{{color:#7c8785;font-size:12px;margin:0 0 18px}}
 table{{border-collapse:collapse;width:100%;background:#fbfcfb;
        box-shadow:0 1px 2px rgba(0,0,0,.06)}}
 th{{text-align:left;font-size:10px;letter-spacing:.1em;text-transform:uppercase;
     color:#7c8785;padding:10px 12px;border-bottom:1px solid #101615}}
 td{{padding:8px 12px;border-bottom:1px solid #e6eae9;vertical-align:middle}}
 td.n{{text-align:right;font-variant-numeric:tabular-nums}}
 td.k,td.s{{font-size:12px;color:#4b5654}} td.dim{{color:#7c8785}}
 td.u{{font-size:11px;color:#7c8785;max-width:330px;overflow:hidden;
       text-overflow:ellipsis;white-space:nowrap}}
 tr.bad td{{background:#fdeceb}} tr.warn td{{background:#fdf6e7}}
 tr.bad td.s{{color:#a8281c;font-weight:600}}
 .spark{{display:inline-flex;align-items:flex-end;gap:1px;height:24px}}
 .spark i{{width:3px;background:#0d6f5d;opacity:.55;display:block}}
 .alert{{background:#a8281c;color:#fff;padding:9px 13px;border-radius:3px;
         margin:0 0 14px;font-size:13px}}
 .fine{{background:#0d6f5d;color:#fff;padding:9px 13px;border-radius:3px;
        margin:0 0 14px;font-size:13px}}
 @media(prefers-color-scheme:dark){{
   body{{background:#0a0e0d;color:#edf2f0}} table{{background:#131817}}
   th{{border-bottom-color:#edf2f0;color:#79837f}} td{{border-bottom-color:#262d2b}}
   tr.bad td{{background:#2a1512}} tr.warn td{{background:#241f10}}
 }}
</style>
<h1>Sources</h1>
<p class="meta">{s['generated']} &middot; {len(s['feeds'])} feeds &middot;
 {sum(s['articles_24h'].values())} news articles in 24h &middot;
 {s['articles_total']} total &middot; {s['not_news_24h']} sport/showbiz set aside &middot; refreshes every 5 min</p>
{banner}
<table><thead><tr><th>Outlet</th><th>Feed</th><th>Last ok</th><th>Items</th>
<th>Usual</th><th>Last 24 polls</th><th>State</th><th>URL</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
"""


if __name__ == "__main__":
    s = collect()
    print(text(s))
    if "--html" in sys.argv:
        p = os.path.join(ROOT, "data", "status.html")
        with open(p, "w", encoding="utf-8") as f:
            f.write(html(s))
        print(f"\nwrote {p}")
    sys.exit(1 if any(r["flags"] for r in s["feeds"]) else 0)
