#!/usr/bin/env python3
"""Probe candidate RSS feeds and report which are live.

Checks each URL returns a parseable feed with entries, so the world registry
only carries sources that actually work. Run before curating.

    python3 scripts/probe_feeds.py
"""
import concurrent.futures as cf
import feedparser
import urllib.request

# (region, name, url) candidates spanning the world.
CANDIDATES = [
    # North America
    ("N.America", "NPR", "https://feeds.npr.org/1001/rss.xml"),
    ("N.America", "NPR World", "https://feeds.npr.org/1004/rss.xml"),
    ("N.America", "BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("N.America", "Reuters (via Google)", "https://news.google.com/rss/search?q=when:24h+site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
    # Europe
    ("Europe", "BBC Europe", "https://feeds.bbci.co.uk/news/world/europe/rss.xml"),
    ("Europe", "Le Monde (EN)", "https://www.lemonde.fr/en/rss/une.xml"),
    ("Europe", "Deutsche Welle EN", "https://rss.dw.com/rdf/rss-en-world"),
    ("Europe", "Euronews", "https://www.euronews.com/rss?level=theme&name=news"),
    ("Europe", "The Guardian World", "https://www.theguardian.com/world/rss"),
    ("Europe", "Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    # Asia
    ("Asia", "BBC Asia", "https://feeds.bbci.co.uk/news/world/asia/rss.xml"),
    ("Asia", "SCMP", "https://www.scmp.com/rss/91/feed"),
    ("Asia", "The Hindu", "https://www.thehindu.com/news/international/feeder/default.rss"),
    ("Asia", "Japan Times", "https://www.japantimes.co.jp/feed/"),
    ("Asia", "Straits Times", "https://www.straitstimes.com/news/world/rss.xml"),
    # Middle East
    ("M.East", "Al Jazeera ME", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("M.East", "Times of Israel", "https://www.timesofisrael.com/feed/"),
    ("M.East", "Arab News", "https://www.arabnews.com/rss.xml"),
    # Africa
    ("Africa", "BBC Africa", "https://feeds.bbci.co.uk/news/world/africa/rss.xml"),
    ("Africa", "AllAfrica", "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf"),
    ("Africa", "News24", "https://feeds.news24.com/news24/Africa"),
    # Latin America
    ("LatAm", "BBC LatAm", "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml"),
    ("LatAm", "MercoPress", "https://en.mercopress.com/rss/rss.xml"),
    ("LatAm", "Brazil (Google)", "https://news.google.com/rss?hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    # South Asia / Oceania
    ("S.Asia", "Dawn", "https://www.dawn.com/feeds/world"),
    ("S.Asia", "The News Intl", "https://www.thenews.com.pk/rss/1/1"),
    ("Oceania", "ABC News AU", "https://www.abc.net.au/news/feed/51120/rss.xml"),
    ("Oceania", "RNZ", "https://www.rnz.co.nz/rss/world.xml"),
]


def probe(item):
    region, name, url = item
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read()
        feed = feedparser.parse(body)
        n = len(feed.entries)
        has_full = any(len((e.get("content") or [{}])[0].get("value", "")) > 400
                       for e in feed.entries[:5])
        return (region, name, url, n, has_full, "OK")
    except Exception as e:
        return (region, name, url, 0, False, f"{type(e).__name__}: {str(e)[:40]}")


def main():
    results = []
    with cf.ThreadPoolExecutor(max_workers=10) as pool:
        for r in pool.map(probe, CANDIDATES):
            results.append(r)
            region, name, url, n, full, status = r
            flag = "FULL" if full else ("ok  " if n else "DEAD")
            print(f"  [{flag}] {region:9} {name:22} entries={n:3}  {status}")
    live = [r for r in results if r[3] > 0]
    print(f"\n{len(live)}/{len(results)} live")
    print("\nLIVE WITH FULL TEXT (best for longform):")
    for region, name, url, n, full, _ in live:
        if full:
            print(f'    ("{region}", "{name}", "{url}"),')


if __name__ == "__main__":
    main()
