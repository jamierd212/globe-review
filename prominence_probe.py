#!/usr/bin/env python3
"""
prominence_probe.py - does a homepage snapshot actually yield a salience signal?

Two questions this answers, neither of which RSS can:

  1. robots  - for the user agent you intend to use, are you allowed to fetch
               the homepage at all? (Run this before promising an outlet list.)
  2. tiers   - do the headline font sizes on a news homepage cluster into a
               small number of discrete editorial ranks - splash, lead,
               secondary, list item - or is it a continuum? The whole
               homepage-as-salience idea rests on the answer being "tiers".

Usage:
    pip install playwright && playwright install chromium
    python3 prominence_probe.py robots
    python3 prominence_probe.py tiers https://news.sky.com/
    python3 prominence_probe.py tiers https://news.sky.com/ --json out.json

Absolute pixel sizes are NOT comparable between outlets - the Guardian's splash
type is not the Sun's. Everything below is normalised within a single page, and
the output to compare across outlets is the tier index, never the px value.
"""

import sys, json, re, urllib.request, urllib.error, socket

socket.setdefaulttimeout(20)

SITES = {
    "BBC":        "https://www.bbc.co.uk/news",
    "Guardian":   "https://www.theguardian.com/uk",
    "Telegraph":  "https://www.telegraph.co.uk/",
    "Express":    "https://www.express.co.uk/",
    "Mail":       "https://www.dailymail.co.uk/home/index.html",
    "Sun":        "https://www.thesun.co.uk/",
    "Mirror":     "https://www.mirror.co.uk/",
    "Times":      "https://www.thetimes.com/",
    "Independent":"https://www.independent.co.uk/",
    "Sky":        "https://news.sky.com/",
    "Metro":      "https://metro.co.uk/",
    "FT":         "https://www.ft.com/",
    "i":          "https://inews.co.uk/",
}

# Identify yourself honestly. If you are going to obey robots.txt - and you are -
# then say who you are rather than dressing up as a browser.
UA = "FrontPageMonitor/0.1 (+https://example.org/about; research; contact@example.org)"


# ---------------------------------------------------------------- robots ----

def parse_robots(text):
    """{agent: [(allow|disallow, path), ...]} - last-match-wins is applied later."""
    groups, agents = {}, []
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if agents and agents[-1][1]:      # a rule closed the previous group
                agents = []
            agents.append([v.lower(), False])
            groups.setdefault(v.lower(), [])
        elif k in ("allow", "disallow") and agents:
            for a in agents:
                a[1] = True
                groups[a[0]].append((k, v))
    return groups


def allowed(groups, agent, path):
    rules = groups.get(agent.lower())
    via = agent
    if rules is None:
        rules = groups.get("*", [])
        via = "*"
    best = None
    for kind, val in rules:
        if kind == "disallow" and val == "":
            continue                          # empty Disallow means allow all
        if val and path.startswith(val.rstrip("$")):
            if best is None or len(val) > len(best[1]):
                best = (kind, val)
    verdict = "allowed" if (best is None or best[0] == "allow") else "BLOCKED"
    return verdict, via


def cmd_robots():
    checks = ["*", "FrontPageMonitor", "ClaudeBot", "GPTBot", "CCBot",
              "Google-Extended", "PerplexityBot", "Bytespider"]
    print(f"{'outlet':<12} {'home':<8} " + " ".join(f"{c[:9]:<10}" for c in checks))
    for name, url in SITES.items():
        root = re.match(r"https?://[^/]+", url).group(0)
        path = url[len(root):] or "/"
        try:
            req = urllib.request.Request(root + "/robots.txt",
                                         headers={"User-Agent": UA})
            with urllib.request.urlopen(req) as f:
                txt = f.read(500_000).decode("utf-8", "replace")
                code = f.status
        except urllib.error.HTTPError as e:
            code, txt = e.code, ""
        except Exception as e:
            code, txt = type(e).__name__, ""
        if not txt:
            print(f"{name:<12} {str(code):<8} (no robots.txt retrieved)")
            continue
        g = parse_robots(txt)
        cells = []
        for c in checks:
            v, via = allowed(g, c, path)
            cells.append(("!" if v == "BLOCKED" else ".") +
                         (v[:1] if via == c else v[:1].lower()))
        print(f"{name:<12} {str(code):<8} " + " ".join(f"{x:<10}" for x in cells))
    print("\n  '.a' allowed  '!B' BLOCKED   lower case = inherited from the * group")
    print("  Run this for the agent you will ACTUALLY use, and treat a blocked")
    print("  outlet as excluded from the product rather than as an empty one.")


# ----------------------------------------------------------------- tiers ----

EXTRACT_JS = r"""
() => {
  const out = [];
  const seen = new Set();
  for (const a of document.querySelectorAll('a')) {
    const t = (a.innerText || '').trim().replace(/\s+/g, ' ');
    if (t.length < 18 || t.length > 220) continue;      // not a headline
    const r = a.getBoundingClientRect();
    if (r.width < 60 || r.height < 10) continue;        // not rendered
    const cs = getComputedStyle(a);
    if (cs.visibility === 'hidden' || cs.display === 'none') continue;
    // the anchor often wraps the text in a heading; take the largest type inside
    let fs = parseFloat(cs.fontSize) || 0;
    for (const el of a.querySelectorAll('h1,h2,h3,h4,span,div,p')) {
      const s = parseFloat(getComputedStyle(el).fontSize) || 0;
      const rr = el.getBoundingClientRect();
      if (s > fs && rr.width > 40) fs = s;
    }
    const key = t.slice(0, 60) + '|' + Math.round(r.top);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({
      text: t.slice(0, 120),
      fs: Math.round(fs * 10) / 10,
      weight: cs.fontWeight,
      x: Math.round(r.left + window.scrollX),
      y: Math.round(r.top + window.scrollY),
      w: Math.round(r.width),
      h: Math.round(r.height),
      area: Math.round(r.width * r.height),
      href: a.href
    });
  }
  return { items: out, pageHeight: document.documentElement.scrollHeight,
           viewport: window.innerHeight };
}
"""


def cluster_1d(values, max_k=6):
    """Split sorted values at the largest relative gaps. News pages are built
    from a handful of type sizes, so gap-splitting beats k-means here and has
    no random seed to argue about."""
    vs = sorted(set(values))
    if len(vs) <= 1:
        return [vs]
    gaps = sorted(((vs[i + 1] / vs[i], i) for i in range(len(vs) - 1)),
                  reverse=True)
    cuts = sorted(i for _, i in gaps[:max_k - 1] if _ > 1.08)
    tiers, start = [], 0
    for c in cuts:
        tiers.append(vs[start:c + 1]); start = c + 1
    tiers.append(vs[start:])
    return [t for t in tiers if t]


def cmd_tiers(url, dump=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("pip install playwright && playwright install chromium")

    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 1000}, user_agent=UA)
        pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        pg.wait_for_timeout(2500)                 # let lazy layout settle
        data = pg.evaluate(EXTRACT_JS)
        b.close()

    items = data["items"]
    if not items:
        sys.exit("no headline-shaped links found - the extractor needs tuning "
                 "for this outlet")

    tiers = cluster_1d([i["fs"] for i in items])
    tier_of = {}
    for idx, t in enumerate(reversed(tiers)):     # 0 = biggest type
        for v in t:
            tier_of[v] = idx

    for i in items:
        i["tier"] = tier_of[i["fs"]]
    items.sort(key=lambda i: (i["tier"], i["y"]))

    fold = data["viewport"]
    print(f"\n{url}")
    print(f"{len(items)} headline links, page {data['pageHeight']}px, "
          f"{len(tiers)} type tiers\n")
    for idx, t in enumerate(reversed(tiers)):
        rows = [i for i in items if i["tier"] == idx]
        above = sum(1 for r in rows if r["y"] < fold)
        print(f"  tier {idx}  {min(t):>5.1f}-{max(t):>5.1f}px  "
              f"n={len(rows):<4} above-fold={above}")
        for r in rows[:3]:
            print(f"           y={r['y']:<6} {r['text'][:74]}")
    print("\n  A clean result is 3-6 tiers with a small top tier and a long")
    print("  tail - that is the desk's own ranking, readable directly.")
    print("  A continuum of sizes means this outlet needs container position")
    print("  rather than type size as its prominence signal.\n")

    if dump:
        json.dump({"url": url, "items": items,
                   "tiers": [[min(t), max(t)] for t in reversed(tiers)]},
                  open(dump, "w"), indent=1)
        print(f"  wrote {dump}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in ("robots", "tiers"):
        sys.exit(__doc__)
    if sys.argv[1] == "robots":
        cmd_robots()
    else:
        if len(sys.argv) < 3:
            sys.exit("usage: prominence_probe.py tiers <url> [--json out.json]")
        out = None
        if "--json" in sys.argv:
            out = sys.argv[sys.argv.index("--json") + 1]
        cmd_tiers(sys.argv[2], out)
