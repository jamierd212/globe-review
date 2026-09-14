"""Schema and connection.

Two decisions here are load-bearing and hard to change later.

1. `observation` records one row per article per feed per poll, with its
   POSITION in the feed. That table is the substrate for the dwell proxy: how
   long an outlet keeps a story near the top of its own front-page feed. It is
   also the only thing here that cannot be reconstructed afterwards - a poll
   you did not make is gone - so it is written from the very first run, months
   before anything reads it.

2. `poll` records every attempt, including the failures. An outlet that
   quietly stops appearing looks exactly like an outlet that has gone quiet,
   and without this table there is no way to tell them apart.
"""

import sqlite3
import os

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS outlet (
  id       TEXT PRIMARY KEY,
  name     TEXT NOT NULL,
  leaning  REAL NOT NULL,      -- press-map x: left (-1) to right (+1)
  market   REAL NOT NULL,      -- press-map y: popular (-1) to broadsheet (+1)
  weight   REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS feed (
  id        INTEGER PRIMARY KEY,
  outlet_id TEXT NOT NULL REFERENCES outlet(id),
  url       TEXT NOT NULL UNIQUE,
  kind      TEXT NOT NULL,     -- 'top' (curated front page) | 'section'
  curated   INTEGER            -- 1/0/NULL-unknown: does position mean anything?
);

CREATE TABLE IF NOT EXISTS article (
  id           INTEGER PRIMARY KEY,
  outlet_id    TEXT NOT NULL REFERENCES outlet(id),
  url_canon    TEXT NOT NULL,
  guid         TEXT,
  title        TEXT NOT NULL,
  standfirst   TEXT,
  published_at TEXT,           -- ISO8601 UTC, from the feed; may be absent
  first_seen   TEXT NOT NULL,  -- ISO8601 UTC, when WE first saw it
  title_sig    TEXT NOT NULL,  -- normalised-title hash, for exact-dup catching
  UNIQUE (outlet_id, url_canon)
);
CREATE INDEX IF NOT EXISTS ix_article_seen ON article (first_seen);
CREATE INDEX IF NOT EXISTS ix_article_sig  ON article (title_sig);

CREATE TABLE IF NOT EXISTS poll (
  id          INTEGER PRIMARY KEY,
  feed_id     INTEGER NOT NULL REFERENCES feed(id),
  polled_at   TEXT NOT NULL,
  http_status TEXT,            -- code, or the exception name
  n_items     INTEGER NOT NULL DEFAULT 0,
  n_new       INTEGER NOT NULL DEFAULT 0,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS ix_poll_feed ON poll (feed_id, polled_at);

CREATE TABLE IF NOT EXISTS observation (
  poll_id    INTEGER NOT NULL REFERENCES poll(id),
  feed_id    INTEGER NOT NULL REFERENCES feed(id),
  article_id INTEGER NOT NULL REFERENCES article(id),
  polled_at  TEXT NOT NULL,
  position   INTEGER NOT NULL, -- 0 = first item in the feed
  PRIMARY KEY (poll_id, article_id)
);
CREATE INDEX IF NOT EXISTS ix_obs_article ON observation (article_id, polled_at);
CREATE INDEX IF NOT EXISTS ix_obs_feed    ON observation (feed_id, polled_at);

-- A STORY is what the globe draws: one running event, covered by several
-- outlets, named from the coverage itself. "UK sanctions on Israeli
-- settlements", not "Middle East". Stories are born when a cluster forms and
-- die when coverage stops, which is days to weeks - the churn is the point,
-- and a globe showing the same labels every morning would be a dead object.
--
-- An ISSUE is the standing taxonomy entry a story is tagged with. It is NOT
-- drawn. It exists so that March is comparable with September: a story cannot
-- carry a six-month line because it does not live that long, and its issue
-- can. One story, one primary issue, optionally more.
CREATE TABLE IF NOT EXISTS story (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,      -- from the coverage, neutral; see cluster/naming.md
  target      TEXT NOT NULL,      -- what stance is favourability TOWARD
  issue_id    TEXT,               -- taxonomy.json id, or NULL = 'none of these'
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL,
  status      TEXT NOT NULL,      -- 'live' | 'dormant' | 'closed'
  centroid    BLOB,               -- packed float32, for next day's matching
  n_articles  INTEGER NOT NULL DEFAULT 0,
  target_conf TEXT                -- 'inherited' | 'generated' | 'reviewed'
);
CREATE INDEX IF NOT EXISTS ix_story_status ON story (status, last_seen);
CREATE INDEX IF NOT EXISTS ix_story_issue  ON story (issue_id);

CREATE TABLE IF NOT EXISTS story_member (
  article_id INTEGER PRIMARY KEY REFERENCES article(id),
  story_id   INTEGER NOT NULL REFERENCES story(id),
  similarity REAL,
  assigned_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_member_story ON story_member (story_id);

-- One row per article per issue. The unit is the (issue, target) pair, not the
-- article: a piece about a returns agreement is favourable toward the Home
-- Office and hostile toward the people being returned, and one number for it
-- would mean nothing.
--
-- rubric_version and model are stamped on every row because a change to either
-- means the archive was produced by two different instruments, and playback
-- has to be able to mark that boundary rather than smooth over it.
CREATE TABLE IF NOT EXISTS article_score (
  article_id     INTEGER NOT NULL REFERENCES article(id),
  issue_id       TEXT,                -- taxonomy id, or NULL for a story-only target
  story_id       INTEGER REFERENCES story(id),
  stance         REAL,                -- -2..2, or NULL for an abstention
  tone           REAL,
  confidence     TEXT,                -- high | medium | low
  quote          TEXT,                -- verbatim span, or NULL
  desk           TEXT,
  reason         TEXT,
  rubric_version TEXT NOT NULL,
  model          TEXT NOT NULL,
  scored_at      TEXT NOT NULL,
  PRIMARY KEY (article_id, issue_id)
);
CREATE INDEX IF NOT EXISTS ix_score_story ON article_score (story_id);

-- near-duplicate clusters: the same agency copy under different mastheads
CREATE TABLE IF NOT EXISTS dup_member (
  article_id INTEGER PRIMARY KEY REFERENCES article(id),
  group_id   INTEGER NOT NULL,
  method     TEXT NOT NULL     -- 'url' | 'title' | 'jaccard'
);
CREATE INDEX IF NOT EXISTS ix_dup_group ON dup_member (group_id);
"""

DEFAULT_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "data", "ingest.sqlite")


def connect(path=None):
    path = path or DEFAULT_DB
    if path != ":memory:":
        os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def sync_outlets(con, spec):
    """Bring outlet and feed rows into line with outlets.json.

    Feeds are never deleted, only added: a feed that disappears from the config
    still has history attached to it, and dropping the row would orphan every
    observation made through it.
    """
    for o in spec["outlets"]:
        # an outlet we are not allowed to collect is not put in the database at
        # all, so nothing downstream can quietly start counting it
        if o.get("excluded"):
            continue
        con.execute(
            "INSERT INTO outlet (id, name, leaning, market, weight) VALUES (?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, leaning=excluded.leaning, "
            "market=excluded.market, weight=excluded.weight",
            (o["id"], o["name"], o["leaning"], o["market"], o["weight"]))
        for f in o["feeds"]:
            cur = f.get("curated")
            con.execute(
                "INSERT INTO feed (outlet_id, url, kind, curated) VALUES (?,?,?,?) "
                "ON CONFLICT(url) DO UPDATE SET kind=excluded.kind, curated=excluded.curated",
                (o["id"], f["url"], f["kind"],
                 None if cur is None else int(bool(cur))))
    con.commit()
