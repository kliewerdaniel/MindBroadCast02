"""The world source registry: feeds spanning every region.

"Represent the entire world" is a coverage requirement, not a count. This
module is the curated set of live, verified RSS feeds organized by region, so
the station draws from North America, Europe, Asia, the Middle East, Africa,
Latin America, South Asia, and Oceania rather than defaulting to one or two
Anglophone outlets.

Each entry is (region, name, url). Every URL here was probed live
(scripts/probe_feeds.py) and returns a parseable feed. Three carry full article
text (NPR, NPR World, Dawn) and are the best sources for longform; the rest are
summary feeds, still valid for coverage and ranking.

Coverage is deliberately region-balanced: the station rotates across regions
(see AutonomousBroadcaster source rotation) so no single region dominates the
broadcast just because its feed is larger.
"""

from dataclasses import dataclass
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class WorldSource:
    region: str
    name: str
    url: str
    # True when the feed ships full article text (best for multi-fact longform).
    full_text: bool = False


# Region -> [(name, url, full_text)]. Probed live; dead feeds omitted.
WORLD_FEEDS: Dict[str, List[WorldSource]] = {
    "North America": [
        WorldSource("North America", "NPR", "https://feeds.npr.org/1001/rss.xml", True),
        WorldSource("North America", "NPR World", "https://feeds.npr.org/1004/rss.xml", True),
        WorldSource("North America", "BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
        WorldSource("North America", "AP (via Google)",
                    "https://news.google.com/rss/search?q=when:24h+site:apnews.com&hl=en-US&gl=US&ceid=US:en"),
    ],
    "Europe": [
        WorldSource("Europe", "BBC Europe",
                    "https://feeds.bbci.co.uk/news/world/europe/rss.xml"),
        WorldSource("Europe", "Deutsche Welle EN",
                    "https://rss.dw.com/rdf/rss-en-world"),
        WorldSource("Europe", "Euronews",
                    "https://www.euronews.com/rss?level=theme&name=news"),
        WorldSource("Europe", "The Guardian World",
                    "https://www.theguardian.com/world/rss"),
        WorldSource("Europe", "Le Monde (EN)",
                    "https://www.lemonde.fr/en/rss/une.xml"),
    ],
    "Middle East": [
        WorldSource("Middle East", "Al Jazeera",
                    "https://www.aljazeera.com/xml/rss/all.xml"),
        WorldSource("Middle East", "Times of Israel",
                    "https://www.timesofisrael.com/feed/"),
        WorldSource("Middle East", "Arab News",
                    "https://www.arabnews.com/rss.xml"),
    ],
    "Asia": [
        WorldSource("Asia", "BBC Asia",
                    "https://feeds.bbci.co.uk/news/world/asia/rss.xml"),
        WorldSource("Asia", "SCMP", "https://www.scmp.com/rss/91/feed"),
        WorldSource("Asia", "The Hindu",
                    "https://www.thehindu.com/news/international/feeder/default.rss"),
        WorldSource("Asia", "Japan Times",
                    "https://www.japantimes.co.jp/feed/"),
        WorldSource("Asia", "Straits Times",
                    "https://www.straitstimes.com/news/world/rss.xml"),
    ],
    "Africa": [
        WorldSource("Africa", "BBC Africa",
                    "https://feeds.bbci.co.uk/news/world/africa/rss.xml"),
        WorldSource("Africa", "AllAfrica",
                    "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf"),
    ],
    "Latin America": [
        WorldSource("Latin America", "BBC Latin America",
                    "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml"),
        WorldSource("Latin America", "Brazil (Google)",
                    "https://news.google.com/rss?hl=pt-BR&gl=BR&ceid=BR:pt-419"),
    ],
    "South Asia": [
        WorldSource("South Asia", "Dawn", "https://www.dawn.com/feeds/world", True),
        WorldSource("South Asia", "The News International",
                    "https://www.thenews.com.pk/rss/1/1"),
    ],
    "Oceania": [
        WorldSource("Oceania", "ABC News AU",
                    "https://www.abc.net.au/news/feed/51120/rss.xml"),
        WorldSource("Oceania", "RNZ", "https://www.rnz.co.nz/rss/world.xml"),
    ],
}


def all_feeds() -> List[WorldSource]:
    """Every world feed, flattened."""
    return [s for feeds in WORLD_FEEDS.values() for s in feeds]


def regions() -> List[str]:
    return list(WORLD_FEEDS.keys())


def feeds_for_region(region: str) -> List[WorldSource]:
    return WORLD_FEEDS.get(region, [])


def full_text_feeds() -> List[WorldSource]:
    """Feeds that ship full article text — best for multi-fact longform."""
    return [s for s in all_feeds() if s.full_text]


# Convenience tuples for callers that just want (name, url).
def as_tuples() -> List[Tuple[str, str, str]]:
    """[(region, name, url), ...] for every feed."""
    return [(s.region, s.name, s.url) for s in all_feeds()]
