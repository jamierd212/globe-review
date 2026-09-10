"""Agency copy detection.

The week-one question is: how much of the apparent volume is the same PA story
running under ten mastheads? If it is most of it, the premise of measuring
"what the press is talking about" by counting articles is in trouble, and it is
better to know that in week one than in week six.

Two passes, cheapest first:

  url      - the same canonical url under two outlets (syndication)
  title    - identical normalised titles
  jaccard  - token-set overlap above a threshold, which is what actually
             catches wire copy, because desks retitle it lightly

The jaccard pass uses an inverted index on the rarest tokens rather than
comparing every pair. At a few thousand articles a day the quadratic version
would also work, but it stops working exactly when the archive gets
interesting.
"""

import re
import hashlib
from collections import defaultdict

STOP = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "as", "is", "are", "was", "were", "be", "been", "it",
    "its", "that", "this", "these", "those", "he", "she", "they", "his", "her",
    "their", "you", "your", "we", "our", "us", "i", "not", "no", "after",
    "over", "into", "up", "out", "new", "says", "say", "said", "will", "has",
    "have", "had", "who", "what", "how", "why", "amid", "more",
}

_word = re.compile(r"[a-z0-9']+")


def tokens(title):
    t = title.lower().replace("’", "'")
    return [w for w in _word.findall(t) if w not in STOP and len(w) > 2]


def title_sig(title):
    """A hash of the normalised title, for the cheap exact-match pass."""
    return hashlib.sha1(" ".join(sorted(set(tokens(title)))).encode()).hexdigest()[:16]


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / (len(a) + len(b) - inter)


def group(articles, threshold=0.72, min_tokens=4):
    """articles: [{id, outlet_id, url_canon, title}] -> {article_id: group_id}

    Only cross-outlet matches are grouped. A paper running three follow-ups on
    its own story is doing journalism; ten papers running one press release is
    the thing we are measuring.
    """
    parent = {}

    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for a in articles:
        parent.setdefault(a["id"], a["id"])

    method = {}

    by_url = defaultdict(list)
    by_sig = defaultdict(list)
    toks = {}
    for a in articles:
        toks[a["id"]] = set(tokens(a["title"]))
        if a.get("url_canon"):
            by_url[a["url_canon"]].append(a)
        by_sig[title_sig(a["title"])].append(a)

    for bucket, name in ((by_url, "url"), (by_sig, "title")):
        for rows in bucket.values():
            if len(rows) < 2:
                continue
            outlets = {r["outlet_id"] for r in rows}
            if len(outlets) < 2:
                continue
            for r in rows[1:]:
                union(rows[0]["id"], r["id"])
                method.setdefault(r["id"], name)
            method.setdefault(rows[0]["id"], name)

    # inverted index on the rarest token of each title
    df = defaultdict(int)
    for tid, ts in toks.items():
        for t in ts:
            df[t] += 1
    index = defaultdict(list)
    for a in articles:
        ts = toks[a["id"]]
        if len(ts) < min_tokens:
            continue
        for t in sorted(ts, key=lambda w: df[w])[:3]:
            index[t].append(a)

    seen_pairs = set()
    for rows in index.values():
        if len(rows) < 2 or len(rows) > 400:
            continue
        for i in range(len(rows)):
            for j in range(i + 1, len(rows)):
                x, y = rows[i], rows[j]
                if x["outlet_id"] == y["outlet_id"]:
                    continue
                key = (min(x["id"], y["id"]), max(x["id"], y["id"]))
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                if jaccard(toks[x["id"]], toks[y["id"]]) >= threshold:
                    union(x["id"], y["id"])
                    method.setdefault(x["id"], "jaccard")
                    method.setdefault(y["id"], "jaccard")

    return ({a["id"]: find(a["id"]) for a in articles}, method)


def duplication_rate(groups):
    """Share of articles that are not the first instance of their group -
    the headline number for the week-one go/no-go."""
    if not groups:
        return 0.0
    sizes = defaultdict(int)
    for g in groups.values():
        sizes[g] += 1
    dupes = sum(n - 1 for n in sizes.values())
    return dupes / len(groups)
