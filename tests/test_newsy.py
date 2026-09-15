import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ingest.newsy import is_news, split

def test_sport_and_showbiz_are_not_news():
    for u in ["https://bbc.co.uk/sport/football/12345",
              "https://dailymail.co.uk/tvshowbiz/article-1/x",
              "https://theguardian.com/football/2026/sep/13/match",
              "https://mirror.co.uk/sport/boxing/fight",
              "https://metro.co.uk/lifestyle/horoscopes/today",
              "https://thesun.co.uk/puzzles/crossword"]:
        assert not is_news(u), u

def test_real_news_is_kept():
    for u in ["https://bbc.co.uk/news/uk-politics-12345",
              "https://theguardian.com/politics/2026/sep/13/reform",
              "https://telegraph.co.uk/news/2026/09/13/asylum",
              "https://ft.com/content/abc-def",
              "https://independent.co.uk/news/uk/home-news/story",
              "https://theguardian.com/commentisfree/2026/sep/13/column"]:
        assert is_news(u), u

def test_ambiguous_paths_are_kept_not_dropped():
    """Letting sport through costs pennies; dropping real news loses it."""
    for u in ["https://x.co.uk/", "https://x.co.uk/story/123",
              "https://x.co.uk/2026/09/13/something", "", None]:
        assert is_news(u), u

def test_split_keeps_order():
    rows = [{"url_canon": "https://x/news/a"}, {"url_canon": "https://x/sport/b"},
            {"url_canon": "https://x/news/c"}]
    news, other = split(rows)
    assert [r["url_canon"] for r in news] == ["https://x/news/a", "https://x/news/c"]
    assert len(other) == 1

if __name__ == "__main__":
    fails = 0
    for n, fn in sorted(globals().items()):
        if n.startswith("test_") and callable(fn):
            try: fn(); print(f"  ok   {n}")
            except AssertionError as e: fails += 1; print(f"  FAIL {n}: {e}")
            except Exception as e: fails += 1; print(f"  ERR  {n}: {type(e).__name__}: {e}")
    print(("\n%d failure(s)" % fails) if fails else "\nall passed")
    sys.exit(1 if fails else 0)
