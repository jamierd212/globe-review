# Front Page Monitor — ingest

Week one of [the plan](plan.html). No models, no UI, no clustering. This
answers one question: **how many genuinely distinct articles arrive per outlet
per day, and how much of that is the same agency copy under different
mastheads?** If the answer is "not many, and mostly the same", the premise of
measuring press attention by counting articles is in trouble, and it is much
better to know that now.

## First run, in order

```bash
python3 -m ingest.run check        # are the feed urls real? DO THIS FIRST
python3 -m ingest.run poll         # one pass over every feed
python3 -m ingest.run report 1     # the go/no-go table + data/daily.csv
python3 tests/test_ingest.py       # no network needed
python3 tests/test_taxonomy.py     # no network needed
python3 tests/test_identity.py     # no network needed
python3 tests/test_scoring.py      # no network needed
```

## Building the gold set

```bash
python3 -m score.gold draw  --n 300          # pick 300 items to label
python3 -m score.gold label --who jamie      # label them, one at a time
python3 -m score.gold agree --a jamie --b sam
python3 -m score.gold check  --model runs/today.jsonl
```

Two people label the same items **without discussing them first**. If you talk
it through you will agree, and you will have learned nothing. The point isn't
the labels — it's finding which parts of the rubric two people read
differently.

The number that decides whether we can launch is not the average error. It's
whether the error is the **same size for every paper**. An error that is even
across the board can be measured and subtracted. One that hits the Mail harder
than the Guardian means the instrument is unfair to particular titles, and
that has to be fixed before anyone sees it.

`outlets.json` is a list of **educated guesses**. Several feed URLs will be
wrong. `check` tells you which; fix the file from what it reports before
trusting any number downstream.

## What is where

| | |
|---|---|
| `outlets.json` | the source list: feeds, homepage, archived-URL form, press-map position |
| `ingest/db.py` | schema. `observation` and `poll` are the two tables that matter |
| `ingest/feeds.py` | fetch + parse RSS/Atom, stdlib only |
| `ingest/dedup.py` | agency-copy detection: url → title → token overlap |
| `ingest/run.py` | `check`, `poll`, `report` |
| `ingest/status.py` | are all the sources still live? terminal table + `data/status.html` |
| `taxonomy.json` | ~40 standing subjects. A **label on a story**, never drawn on the globe |
| `cluster/embed.py` | turns a headline into numbers. Works with nothing installed |
| `cluster/group.py` | groups today's articles into stories |
| `cluster/run.py` | does that against the collected articles and writes them back |
| `cluster/identity.py` | keeps a story the same story from one day to the next |
| `cluster/naming.md` | how a story gets named, and how it records what it is about |
| `score/rubric.md` | the scoring instruction: scale, desk handling, confidence, abstention, worked examples |
| `score/direction.py` | the direction check — asserts the sign of left-minus-right on the subjects that are not in doubt |
| `score/prompt.py` | builds the model's instructions from the rubric, so they cannot drift |
| `score/run.py` | `tag` (file a story under a subject) then `score` (produce the colour) |
| `score/gold.py` | the gold set: `draw`, `label`, `agree`, `check` |
| `score/sample.py` | picks what to label — equal share per paper, not random |
| `score/agreement.py` | how much two labellers agree, corrected for chance |
| `score/calibrate.py` | how wrong the model is, and whether it is wrong evenly |
| `frames/fit.py` | works out the shapes on the globe, and checks they are honest |
| `prominence_probe.py` | the separate salience question: robots audit + homepage type tiers |

## What one day of real articles looks like

From 930 articles across 13 papers, collected 13 Sept 2026:

- **80% is news-ish; 20% is sport, showbiz or lifestyle** — and it is wildly
  uneven. Mirror 37%, Metro 30%, Sun 28%, against Sky, Express and FT at 0%.
  This matters more than it looks: "share of an outlet's own output" is
  measuring a different denominator for each paper unless the non-news is
  removed first. The taxonomy's "none of these" bucket does that, but it needs
  the model, so it cannot be done in week one.
- **Agency copy is almost invisible from headlines** — 0.1% by title
  similarity, because the papers rewrite everything. "Boris Johnson escapes
  Russian drone strike" (Express) against "Boris Johnson evacuated after train
  drone strike" (Mirror). That is the premise holding up: the headline really
  is the editorialised unit. It also means the wire-copy detector cannot
  measure syndication without the body.
- **Stories form sensibly.** The Johnson train story pulled 12 articles from 10
  papers; Reform 11 from 8; a police appeal 9 from 7.

## Scoring

```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' > .env     # gitignored
.venv/bin/python -m score.run tag              # file each story under a subject
.venv/bin/python -m score.run score --dry      # what will it cost?
.venv/bin/python -m score.run score
```

Only the headline and standfirst are ever sent — never the body, even where we
have it, because every paper has to be scored on the same instrument and three
of the nationals give us nothing else.

`tag` is also the filter. Sport, showbiz and service journalism come back as
"none of these" and never reach the globe.

**Cost, measured rather than guessed** on 300 real articles: $0.64 at list
price, $0.32 batched, about **$0.21 with prompt caching**, which is on. The
rubric is 1,600 tokens and identical on every call, so it was three quarters
of the bill until it was cached.

## Watching the sources

```bash
python3 -m ingest.status --html      # then open data/status.html
```

The failure this catches is the quiet one. A feed that stops answering looks
exactly like a paper having a slow news day — the globe keeps being drawn,
confidently, with a hole in it. Nothing throws an error.

So it asks three questions of every feed, not one:

- **live** — answered in the last few hours
- **flowing** — brought *new* articles, not the same ones again. A paper that
  changes its feed URL often leaves the old one serving a frozen copy forever,
  and that returns 200 and parses perfectly
- **normal** — producing roughly what it usually does, measured against its own
  history rather than a fixed number

The page refreshes itself every five minutes and the hourly job rewrites it.

## Stories are drawn. Subjects are not.

- A shape on the globe is **a story running now** — "UK sanctions on Israeli
  settlements", not "Middle East". Days to weeks, then gone.
- `taxonomy.json` is a **label attached to a story**. Never drawn. It is only
  there so March can be compared with September.
- `cluster/group.py` forms the stories; `cluster/identity.py` keeps each one the
  same object overnight. Get the second wrong and it doesn't look like a bug —
  it looks like a very volatile news cycle.
- Clustering uses **sentence embeddings** where they are installed and falls
  back to counting words where they are not, so it runs on a bare machine. The
  model runs locally: no key, and no article text leaves the laptop.

  ```bash
  python3 -m venv .venv && .venv/bin/pip install sentence-transformers
  ```

  Compared on the same 633 real articles: word counting split the Reform
  donations story in two and merged a Gloucestershire council story with an
  acid attack; the sentence model held Reform together at 28 articles across
  10 papers and found coherent stories the word counter missed entirely. **The
  similarity threshold differs between the two** — 0.16 against 0.58 — and the
  code picks the one matching whichever vectoriser it used.

## The subject list runs ahead of the data on purpose

`taxonomy.json` and `score/rubric.md` need no articles to write and are the
thing that inverts scores if they are wrong, so they come before the first
feed lands rather than after. Two rules do the work:

**Every issue names a stance target.** Stance is favourability *toward the
thing the target names*. "Net zero rollback" is the worked example — the target
is the weakening of the commitments, so a paper cheering the delay is +2. Read
as "net zero" instead, the Guardian and the Express both come out backwards.

**Fifteen of the 42 assert a direction.** Where the left/right ordering is
genuinely not in doubt, `score/direction.py` asserts the *sign* of
left-minus-right on every scoring run — never the magnitude, which moves with
the news. A flat week reports INCONCLUSIVE rather than failing the build. The
other 27 record an expectation that is deliberately not tested, either because
the press agrees (nobody is in favour of high energy bills) or because the
answer depends on who is in government.

## Two things that are deliberate

**`observation` stores feed position on every poll.** It looks redundant now
and nothing reads it yet. It is the substrate for the dwell proxy — how long an
outlet keeps a story near the top of its own front page — and unlike everything
else here it cannot be reconstructed later. A poll you did not make is gone.

**`poll` records failures, not just successes.** An outlet that has quietly
stopped answering looks exactly like an outlet that has gone quiet. Without
this table there is no way to tell the difference, and the globe will keep
drawing a confident picture either way.

## The globe's geometry is checked, not asserted

`frames/fit.py` decides how big each shape is. Three things have to stay true
and each has a test:

- every shape gets the area it should — **worst case 0.6%** off across forty
  shapes, against 1.4% in the prototype
- **no shape ever vanishes**, even when one story takes half the day's news
- **distance from the centre still means how widely a story is carried** —
  rank correlation above 0.8

It does this without numpy. Instead of sprinkling points over the disc and
counting them, it works out exactly where each shape starts and stops along
each horizontal line, which is both faster and exact. A frame takes about a
second.

## Standard library only

The ingest imports nothing outside the stdlib. This job runs unattended on a
schedule for years, and every dependency is something that can rot. `feedparser`
is friendlier; it is not worth the maintenance.

## Still to verify

- Every feed URL in `outlets.json`.
- Whether each outlet's `top` feed is genuinely curated or just newest-first.
  If newest-first, its position signal is worthless and salience for that
  outlet falls back to counts. Poll one for a day and see whether the order
  ever changes without a new item arriving.
- Whether the Guardian is in the Internet Archive at all — see the plan.
