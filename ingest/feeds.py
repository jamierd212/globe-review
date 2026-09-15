"""Fetching and parsing feeds. Standard library only.

feedparser would be friendlier, but this job runs unattended on a schedule for
years and every dependency is a thing that can rot. RSS 2.0 and Atom are small
enough formats to read directly, and the parsing that matters here - title,
link, summary, date, guid, and crucially ORDER - is not the part feedparser is
good at.
"""

import gzip
import re
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
import zlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# Identify honestly. If you are going to obey robots.txt - and you are - then
# say who you are rather than dressing up as somebody's browser.
UA = ("FrontPageMonitor/0.1 (+https://example.org/about; "
      "research; contact@example.org)")

TIMEOUT = 25

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
CONTENT = "{http://purl.org/rss/1.0/modules/content/}"

# tracking parameters carry no identity and would fragment the same article
# into several rows
JUNK_PARAMS = re.compile(
    r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ito$|ns_|CMP$|cmpid$|ICID$|at_)", re.I)


def canon_url(u):
    """A stable identity for an article. Two rules only, because clever
    canonicalisation is how you accidentally merge two different articles."""
    if not u:
        return ""
    u = u.strip()
    try:
        p = urlsplit(u)
    except ValueError:
        return u
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if p.port and p.port not in (80, 443):
        host = f"{host}:{p.port}"
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if not JUNK_PARAMS.match(k)]
    path = p.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(q), ""))


def strip_html(s):
    if not s:
        return ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'")
          .replace("&rsquo;", "'").replace("&lsquo;", "'")
          .replace("&ldquo;", '"').replace("&rdquo;", '"')
          .replace("&mdash;", " - ").replace("&ndash;", " - "))
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)            # RFC 822, the RSS form
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))   # Atom
        except Exception:
            return None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat(timespec="seconds")


def _text(el, *names):
    for n in names:
        f = el.find(n)
        if f is not None:
            if f.text and f.text.strip():
                return f.text
            # Atom <link href="..."/> carries no text
            href = f.get("href")
            if href:
                return href
    return None


def parse(xml_bytes):
    """Return items in FEED ORDER. The order is the point: for a curated
    top-stories feed it is the outlet's own ranking, and it is the cheap dense
    signal the dwell proxy is built on."""
    root = ET.fromstring(xml_bytes)
    nodes = root.findall(".//item")                      # RSS
    if not nodes:
        nodes = root.findall(f".//{ATOM}entry")          # Atom
    items = []
    for n in nodes:
        title = strip_html(_text(n, "title", f"{ATOM}title") or "")
        link = (_text(n, "link", f"{ATOM}link") or "").strip()
        if not title or not link:
            continue
        summary = strip_html(_text(n, "description", f"{ATOM}summary",
                                   f"{ATOM}content", f"{CONTENT}encoded") or "")
        # a "standfirst" is a sentence or two; a full body dumped into the
        # description is not one, and storing it is a copyright question we
        # have no reason to answer
        if len(summary) > 400:
            summary = summary[:400].rsplit(" ", 1)[0] + "…"
        date = parse_date(_text(n, "pubDate", f"{ATOM}published",
                                f"{ATOM}updated", f"{DC}date"))
        guid = (_text(n, "guid", f"{ATOM}id") or "").strip() or None
        items.append({
            "title": title,
            "url": link,
            "url_canon": canon_url(link),
            "standfirst": summary or None,
            "published_at": date,
            "guid": guid,
        })
    return items


def decompress(body, headers):
    """Some feeds send gzip whether or not you asked for it.

    The Independent started doing this between two polls. The body arrives as
    valid gzip, the XML parser chokes, and without the magic-byte check below
    it looks exactly like a malformed feed. Trust the bytes rather than the
    Content-Encoding header, because the outlet that caused this did not set
    one.
    """
    if not body:
        return body
    try:
        if body[:2] == b"\x1f\x8b":
            return gzip.decompress(body)
        enc = (headers.get("Content-Encoding") or "").lower()
        if "gzip" in enc:
            return gzip.decompress(body)
        if "deflate" in enc:
            return zlib.decompress(body, -zlib.MAX_WBITS)
    except Exception:
        return body          # let the parser report it rather than hiding it
    return body


def fetch(url, etag=None, modified=None):
    """Returns (status, body_bytes, headers). Never raises: a dead feed is a
    row in the poll table, not a crashed job."""
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
    })
    if etag:
        req.add_header("If-None-Match", etag)
    if modified:
        req.add_header("If-Modified-Since", modified)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            h = dict(r.headers)
            return r.status, decompress(r.read(8_000_000), h), h
    except urllib.error.HTTPError as e:
        return e.code, b"", dict(getattr(e, "headers", {}) or {})
    except Exception as e:
        return type(e).__name__, b"", {}
