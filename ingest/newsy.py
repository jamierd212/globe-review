"""Is this actually news?

About a fifth of what the papers publish is sport, showbiz, puzzles and
shopping. It is not a flaw in the feeds - it is what a newspaper is - but it
has to come out before anything is counted, for two reasons.

The first is measurement. "Share of a paper's own output" means a different
thing for each title unless the non-news goes first: the Mirror runs 37% of it
and the FT runs none, so counting raw articles would make the Mirror look less
interested in the NHS purely because it also runs showbiz.

The second is cost. The biggest cluster in the first day's data was a Man Utd
match, at 42 articles. Filing and scoring things we then discard was 78% of the
first tagging run.

The URL section is the honest signal - it is the paper's own filing decision,
not our guess about the words. Where the section is ambiguous the article is
kept: letting sport through costs pennies, dropping real news loses it.
"""

import re

SECTIONS = re.compile(
    r"^(sport|football|cricket|rugby|tennis|golf|boxing|f1|formula1|racing|"
    r"fantasy-sports|tvshowbiz|showbiz|celebrity|celebs|entertainment|tv|"
    r"film|movies|music|arts|culture|books|theatre|gaming|games|"
    r"lifestyle|life-style|lifeandstyle|life|food|drink|recipes|fashion|"
    r"beauty|travel|property|homes|garden|motoring|cars|"
    r"money|shopping|deals|offers|vouchers|competitions|"
    r"puzzles|crossword|sudoku|horoscopes|weather|"
    r"dating|relationships|parenting|health-and-fitness)(/|$)", re.I)

# some titles bury the section deeper: /news/sport/..., /uk/football/...
NESTED = re.compile(r"/(sport|football|cricket|rugby|showbiz|tvshowbiz|celebrity|"
                    r"puzzles|crossword|horoscopes)(/|$)", re.I)


def section(url):
    m = re.match(r"https?://[^/]+/([^?#]*)", url or "")
    return (m.group(1) if m else "").strip("/")


def is_news(url):
    """True unless the paper itself filed it somewhere that is not news."""
    path = section(url)
    if not path:
        return True                     # no path to judge by - keep it
    if SECTIONS.match(path):
        return False
    if NESTED.search("/" + path):
        return False
    return True


def split(rows, key="url_canon"):
    """(news, other) - keeps the order."""
    news, other = [], []
    for r in rows:
        (news if is_news(r[key]) else other).append(r)
    return news, other
