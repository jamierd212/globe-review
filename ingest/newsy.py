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


# ---------------------------------------------------------------------------
# When the URL tells us nothing
#
# Items that arrive through Google News carry a Google redirect link, so there
# is no section to read - and the paper whose feed we most need this for runs a
# great deal of sport. The fallback reads the words instead.
#
# It is weaker than the URL and it is meant to be: the URL is the paper's own
# filing decision, this is our guess. It is only used where there is no URL to
# read, and it errs toward keeping things, for the same reason as above.

_SPORT = re.compile(
    r"\b(premier league|champions league|europa league|fa cup|carabao|"
    r"man utd|man city|manchester united|manchester city|arsenal|chelsea|"
    r"liverpool|tottenham|spurs|everton|newcastle united|aston villa|"
    r"west ham|celtic|rangers|old firm|"
    r"transfer (?:news|window|deadline)|wonderkid|striker|midfielder|"
    r"goalkeeper|kick-?off|full-?time|half-?time|penalty shoot|var |"
    r"test match|ashes|six nations|grand slam|wimbledon|open championship|"
    r"formula ?1|f1|grand prix|ufc|heavyweight|title fight|"
    r"solheim|ryder cup|world cup|euro 20\d\d|olympic)\b", re.I)

_SHOW = re.compile(
    r"\b(strictly come dancing|love island|big brother|celebrity|i'm a celeb|"
    r"gogglebox|bake off|x factor|emmerdale|coronation street|eastenders|"
    r"hollyoaks|netflix series|reality star|soap star|"
    r"red carpet|oscars|baftas|brit awards|world tour|new album|"
    r"girlfriend|boyfriend|split from|dating|romance|baby joy|"
    r"shows off|stuns in|flaunts|opens up about)\b", re.I)

_LIFE = re.compile(
    r"\b(recipe|horoscope|your stars|best buys?|shopping|deal of the|"
    r"how to (?:clean|cook|save|get)|money saving|travel deal|"
    r"skincare|beauty|wordle|quiz|puzzle|crossword)\b", re.I)


def is_news_text(title, standfirst=None):
    """Weaker than is_news(). Only for items with no readable URL section."""
    t = (title or "") + " " + (standfirst or "")
    return not (_SPORT.search(t) or _SHOW.search(t) or _LIFE.search(t))


def is_news_row(row):
    """The one to call. Uses the paper's own filing where we have it, the words
    where we do not."""
    url = row.get("url_canon") or row.get("url") or ""
    if section(url) and "news.google.com" not in url:
        return is_news(url)
    return is_news_text(row.get("title"), row.get("standfirst"))
