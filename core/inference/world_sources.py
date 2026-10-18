"""The world source registry: feeds spanning every region.

"Represent the entire world" is a coverage requirement, not a count. This
module is the curated set of live, verified RSS feeds organized by region, so
the station draws from North America, Europe, Asia, the Middle East, Africa,
Latin America, South Asia, and Oceania rather than defaulting to one or two
Anglophone outlets.

Every URL here was probed live (scripts/probe_candidates.py, which reports
which candidates answer with a parseable feed and how many entries they carry)
and returns a real feed. Candidates that failed are deliberately absent rather
than shipped and left to fail at fetch time. Many entries carry full article
text, which is what makes multi-fact longform broadcasts possible; the rest are
summary feeds, still valid for coverage and ranking.

Region balance is a property of the ROTATION, not of the list. Regions differ
in how many feeds they have (North America has far more outlets than Oceania),
so listing them region by region would make a listener hear one region for
dozens of cycles before another appeared. `rotation_order()` interleaves the
regions instead: one feed from each region in turn, round after round. Over
time each region's share of airtime still follows its share of feeds — that is
fair, since a region with more outlets has more to report — but no region
disappears for long stretches, and consecutive cycles come from different
regions rather than the same one.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class WorldSource:
    region: str
    name: str
    url: str
    # True when the feed ships full article text (best for multi-fact longform).
    full_text: bool = False


# Region -> [WorldSource]. Probed live; dead feeds omitted.
WORLD_FEEDS: dict[str, list[WorldSource]] = {
    "North America": [
        WorldSource("North America", "NPR",
                    "https://feeds.npr.org/1001/rss.xml", True),
        WorldSource("North America", "NPR World",
                    "https://feeds.npr.org/1004/rss.xml", True),
        WorldSource("North America", "BBC News",
                    "https://feeds.bbci.co.uk/news/rss.xml"),
        WorldSource("North America", "AP (via Google)",
                    "https://news.google.com/rss/search?q=when:24h+site:apnews.com&hl=en-US&gl=US&ceid=US:en"),
        WorldSource("North America", "Reuters (via Google)",
                    "https://news.google.com/rss/search?q=when:24h+site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
        WorldSource("North America", "NYT World",
                    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
        WorldSource("North America", "NYT US",
                    "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
        WorldSource("North America", "NYT Business",
                    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
        WorldSource("North America", "NYT Technology",
                    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
        # CNN's feeds are deliberately absent: rss.cnn.com only serves plain
        # HTTP (https:// returns nothing), and the registry is HTTPS-only so a
        # feed cannot be downgraded to cleartext in transit.
        WorldSource("North America", "NPR Politics",
                    "https://feeds.npr.org/1014/rss.xml", True),
        WorldSource("North America", "NPR Business",
                    "https://feeds.npr.org/1006/rss.xml", True),
        WorldSource("North America", "NPR Technology",
                    "https://feeds.npr.org/1019/rss.xml", True),
        WorldSource("North America", "NPR Science",
                    "https://feeds.npr.org/1007/rss.xml", True),
        WorldSource("North America", "NPR Health",
                    "https://feeds.npr.org/1128/rss.xml", True),
        WorldSource("North America", "PBS NewsHour",
                    "https://www.pbs.org/newshour/feeds/rss/headlines"),
        WorldSource("North America", "ABC News (US)",
                    "https://abcnews.go.com/abcnews/topstories"),
        WorldSource("North America", "CBS News",
                    "https://www.cbsnews.com/latest/rss/main"),
        WorldSource("North America", "NBC News",
                    "https://feeds.nbcnews.com/nbcnews/public/news"),
        WorldSource("North America", "Politico",
                    "https://rss.politico.com/politics-news.xml", True),
        WorldSource("North America", "The Hill",
                    "https://thehill.com/feed/"),
        WorldSource("North America", "Axios",
                    "https://api.axios.com/feed/", True),
        WorldSource("North America", "ProPublica",
                    "https://www.propublica.org/feeds/propublica/main", True),
        WorldSource("North America", "The Atlantic",
                    "https://www.theatlantic.com/feed/all/", True),
        WorldSource("North America", "Foreign Policy",
                    "https://foreignpolicy.com/feed/"),
        WorldSource("North America", "CBC World",
                    "https://www.cbc.ca/webfeed/rss/rss-world"),
        WorldSource("North America", "CBC Top Stories",
                    "https://www.cbc.ca/webfeed/rss/rss-topstories"),
        WorldSource("North America", "Global News CA",
                    "https://globalnews.ca/feed/"),
        WorldSource("North America", "The Globe and Mail",
                    "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/", True),
        WorldSource("North America", "Mexico News Daily",
                    "https://mexiconewsdaily.com/feed/", True),
        WorldSource("North America", "Ars Technica",
                    "https://feeds.arstechnica.com/arstechnica/index", True),
        WorldSource("North America", "The Verge",
                    "https://www.theverge.com/rss/index.xml", True),
        WorldSource("North America", "Wired",
                    "https://www.wired.com/feed/rss"),
        WorldSource("North America", "MIT Tech Review",
                    "https://www.technologyreview.com/feed/", True),
        WorldSource("North America", "Science Daily",
                    "https://www.sciencedaily.com/rss/all.xml"),
        WorldSource("North America", "Nature News",
                    "https://www.nature.com/nature.rss"),
        WorldSource("North America", "NASA Breaking News",
                    "https://www.nasa.gov/rss/dyn/breaking_news.rss", True),
        WorldSource("North America", "STAT News",
                    "https://www.statnews.com/feed/", True),
        WorldSource("North America", "KFF Health News",
                    "https://kffhealthnews.org/feed/", True),
        WorldSource("North America", "Bellingcat",
                    "https://www.bellingcat.com/feed/", True),
        WorldSource("North America", "The Intercept",
                    "https://theintercept.com/feed/?rss", True),
        WorldSource("North America", "Vox",
                    "https://www.vox.com/rss/index.xml", True),
        WorldSource("North America", "Slate",
                    "https://slate.com/feeds/all.rss"),
        WorldSource("North America", "The Conversation",
                    "https://theconversation.com/articles.atom", True),
        WorldSource("North America", "MarketWatch",
                    "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        WorldSource("North America", "CNBC World",
                    "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362"),
        WorldSource("North America", "Federal Reserve",
                    "https://www.federalreserve.gov/feeds/press_all.xml"),
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
        WorldSource("Europe", "BBC Business",
                    "https://feeds.bbci.co.uk/news/business/rss.xml"),
        WorldSource("Europe", "BBC Technology",
                    "https://feeds.bbci.co.uk/news/technology/rss.xml"),
        WorldSource("Europe", "BBC Science",
                    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
        WorldSource("Europe", "BBC Health",
                    "https://feeds.bbci.co.uk/news/health/rss.xml"),
        WorldSource("Europe", "BBC Politics",
                    "https://feeds.bbci.co.uk/news/politics/rss.xml"),
        WorldSource("Europe", "BBC UK",
                    "https://feeds.bbci.co.uk/news/uk/rss.xml"),
        WorldSource("Europe", "The Guardian UK",
                    "https://www.theguardian.com/uk-news/rss"),
        WorldSource("Europe", "The Guardian Business",
                    "https://www.theguardian.com/uk/business/rss"),
        WorldSource("Europe", "The Guardian Technology",
                    "https://www.theguardian.com/uk/technology/rss"),
        WorldSource("Europe", "The Guardian Environment",
                    "https://www.theguardian.com/environment/rss"),
        WorldSource("Europe", "Financial Times",
                    "https://www.ft.com/rss/home"),
        WorldSource("Europe", "The Economist",
                    "https://www.economist.com/the-world-this-week/rss.xml"),
        WorldSource("Europe", "Sky News",
                    "https://feeds.skynews.com/feeds/rss/world.xml"),
        WorldSource("Europe", "Sky News UK",
                    "https://feeds.skynews.com/feeds/rss/uk.xml"),
        WorldSource("Europe", "France 24",
                    "https://www.france24.com/en/rss"),
        WorldSource("Europe", "RFI English",
                    "https://www.rfi.fr/en/rss"),
        WorldSource("Europe", "Der Spiegel EN",
                    "https://www.spiegel.de/international/index.rss"),
        WorldSource("Europe", "Deutsche Welle Europe",
                    "https://rss.dw.com/rdf/rss-en-eu"),
        WorldSource("Europe", "Deutsche Welle Germany",
                    "https://rss.dw.com/rdf/rss-en-ger"),
        WorldSource("Europe", "Politico Europe",
                    "https://www.politico.eu/feed/", True),
        WorldSource("Europe", "EUobserver",
                    "https://euobserver.com/rss", True),
        WorldSource("Europe", "El Pais English",
                    "https://feeds.elpais.com/mrss-s/pages/ep/site/english.elpais.com/portada",
                    True),
        WorldSource("Europe", "Le Figaro EN",
                    "https://www.lefigaro.fr/rss/figaro_actualites.xml"),
        WorldSource("Europe", "ANSA English",
                    "https://www.ansa.it/english/news/english_rss.xml"),
        WorldSource("Europe", "Dutch News",
                    "https://www.dutchnews.nl/feed/"),
        WorldSource("Europe", "The Local Germany",
                    "https://www.thelocal.de/feeds/rss.php"),
        WorldSource("Europe", "Notes from Poland",
                    "https://notesfrompoland.com/feed/", True),
        WorldSource("Europe", "Ukrinform",
                    "https://www.ukrinform.net/rss/block-lastnews"),
        WorldSource("Europe", "Reuters Europe (Google)",
                    "https://news.google.com/rss/search?q=when:24h+site:reuters.com+europe&hl=en-US&gl=US&ceid=US:en"),
        WorldSource("Europe", "Irish Times",
                    "https://www.irishtimes.com/arc/outboundfeeds/rss/", True),
        WorldSource("Europe", "RTÉ News",
                    "https://www.rte.ie/feeds/rss/?index=/news/"),
        WorldSource("Europe", "The Nordic Page",
                    "https://www.tnp.no/feed"),
        WorldSource("Europe", "The Moscow Times",
                    "https://www.themoscowtimes.com/rss/news"),
    ],
    "Middle East": [
        WorldSource("Middle East", "Al Jazeera",
                    "https://www.aljazeera.com/xml/rss/all.xml"),
        WorldSource("Middle East", "Times of Israel",
                    "https://www.timesofisrael.com/feed/"),
        WorldSource("Middle East", "Arab News",
                    "https://www.arabnews.com/rss.xml"),
        WorldSource("Middle East", "The Jerusalem Post",
                    "https://www.jpost.com/rss/rssfeedsfrontpage.aspx"),
        WorldSource("Middle East", "Al-Monitor",
                    "https://www.al-monitor.com/rss"),
        WorldSource("Middle East", "Egypt Independent",
                    "https://egyptindependent.com/feed/", True),
    ],
    "Asia": [
        WorldSource("Asia", "BBC Asia",
                    "https://feeds.bbci.co.uk/news/world/asia/rss.xml"),
        WorldSource("Asia", "SCMP",
                    "https://www.scmp.com/rss/91/feed"),
        WorldSource("Asia", "The Hindu",
                    "https://www.thehindu.com/news/international/feeder/default.rss"),
        WorldSource("Asia", "Japan Times",
                    "https://www.japantimes.co.jp/feed/"),
        WorldSource("Asia", "Straits Times",
                    "https://www.straitstimes.com/news/world/rss.xml"),
        WorldSource("Asia", "Nikkei Asia",
                    "https://asia.nikkei.com/rss/feed/nar"),
        WorldSource("Asia", "Channel News Asia",
                    "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml"),
        WorldSource("Asia", "The Straits Times Asia",
                    "https://www.straitstimes.com/news/asia/rss.xml"),
        WorldSource("Asia", "Korea Herald",
                    "https://www.koreaherald.com/rss/newsAll"),
        WorldSource("Asia", "The Korea Times",
                    "https://www.koreatimes.co.kr/www/rss/rss.xml", True),
        WorldSource("Asia", "Yonhap",
                    "https://en.yna.co.kr/RSS/news.xml"),
        WorldSource("Asia", "Global Times",
                    "https://www.globaltimes.cn/rss/outbrain.xml"),
        WorldSource("Asia", "Taipei Times",
                    "https://www.taipeitimes.com/xml/index.rss"),
        WorldSource("Asia", "Bangkok Post",
                    "https://www.bangkokpost.com/rss/data/topstories.xml"),
        WorldSource("Asia", "Rappler",
                    "https://www.rappler.com/feed/", True),
        WorldSource("Asia", "Inquirer",
                    "https://newsinfo.inquirer.net/feed"),
        WorldSource("Asia", "Vietnam News",
                    "https://vietnamnews.vn/rss/home.rss"),
        WorldSource("Asia", "VnExpress International",
                    "https://e.vnexpress.net/rss/news.rss"),
        WorldSource("Asia", "The Diplomat",
                    "https://thediplomat.com/feed/"),
        WorldSource("Asia", "Malay Mail",
                    "https://www.malaymail.com/feed/rss/malaysia", True),
        WorldSource("Asia", "Asia Times",
                    "https://asiatimes.com/feed/"),
        WorldSource("Asia", "The Irrawaddy",
                    "https://www.irrawaddy.com/feed"),
        WorldSource("Asia", "The Kathmandu Post",
                    "https://kathmandupost.com/rss"),
    ],
    "Africa": [
        WorldSource("Africa", "BBC Africa",
                    "https://feeds.bbci.co.uk/news/world/africa/rss.xml"),
        WorldSource("Africa", "AllAfrica",
                    "https://allafrica.com/tools/headlines/rdf/latest/headlines.rdf"),
        WorldSource("Africa", "The Africa Report",
                    "https://www.theafricareport.com/feed/"),
        WorldSource("Africa", "African Business",
                    "https://african.business/feed", True),
        WorldSource("Africa", "African Arguments",
                    "https://africanarguments.org/feed/", True),
        WorldSource("Africa", "The Standard Kenya",
                    "https://www.standardmedia.co.ke/rss/headlines.php"),
        WorldSource("Africa", "Vanguard Nigeria",
                    "https://www.vanguardngr.com/feed/", True),
        WorldSource("Africa", "Premium Times",
                    "https://www.premiumtimesng.com/feed"),
        WorldSource("Africa", "The Punch",
                    "https://punchng.com/feed/"),
        WorldSource("Africa", "Zimbabwe Situation",
                    "https://www.zimbabwesituation.com/feed/", True),
    ],
    "Latin America": [
        WorldSource("Latin America", "BBC Latin America",
                    "https://feeds.bbci.co.uk/news/world/latin_america/rss.xml"),
        WorldSource("Latin America", "Brazil (Google)",
                    "https://news.google.com/rss?hl=pt-BR&gl=BR&ceid=BR:pt-419"),
        WorldSource("Latin America", "Buenos Aires Times",
                    "https://www.batimes.com.ar/feed"),
        WorldSource("Latin America", "Buenos Aires Herald",
                    "https://buenosairesherald.com/feed", True),
        WorldSource("Latin America", "Folha de S.Paulo",
                    "https://feeds.folha.uol.com.br/internacional/en/rss091.xml"),
        WorldSource("Latin America", "Rio Times",
                    "https://riotimesonline.com/feed/"),
        WorldSource("Latin America", "Reforma",
                    "https://www.reforma.com/rss/portada.xml"),
        WorldSource("Latin America", "El Tiempo Colombia",
                    "https://www.eltiempo.com/rss/colombia.xml"),
        WorldSource("Latin America", "Colombia Reports",
                    "https://colombiareports.com/feed/", True),
        WorldSource("Latin America", "El Comercio Peru",
                    "https://elcomercio.pe/arcio/rss/", True),
        WorldSource("Latin America", "Havana Times",
                    "https://havanatimes.org/feed/"),
        WorldSource("Latin America", "14ymedio",
                    "https://www.14ymedio.com/rss/"),
        WorldSource("Latin America", "Dominican Today",
                    "https://dominicantoday.com/feed/", True),
        WorldSource("Latin America", "Jamaica Gleaner",
                    "https://jamaica-gleaner.com/feed/rss.xml"),
    ],
    "South Asia": [
        WorldSource("South Asia", "Dawn",
                    "https://www.dawn.com/feeds/world", True),
        WorldSource("South Asia", "The News International",
                    "https://www.thenews.com.pk/rss/1/1"),
        WorldSource("South Asia", "The Hindu National",
                    "https://www.thehindu.com/news/national/feeder/default.rss"),
        WorldSource("South Asia", "Times of India",
                    "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
        WorldSource("South Asia", "Hindustan Times",
                    "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
        WorldSource("South Asia", "Indian Express",
                    "https://indianexpress.com/feed/"),
        WorldSource("South Asia", "NDTV",
                    "https://feeds.feedburner.com/ndtvnews-top-stories"),
        WorldSource("South Asia", "The Economic Times",
                    "https://economictimes.indiatimes.com/rssfeedstopstories.cms"),
        WorldSource("South Asia", "Livemint",
                    "https://www.livemint.com/rss/news"),
        WorldSource("South Asia", "Dawn Pakistan",
                    "https://www.dawn.com/feeds/home", True),
        WorldSource("South Asia", "The Express Tribune",
                    "https://tribune.com.pk/feed/home", True),
        WorldSource("South Asia", "The Daily Star BD",
                    "https://www.thedailystar.net/frontpage/rss.xml"),
        WorldSource("South Asia", "The Island (Sri Lanka)",
                    "https://island.lk/feed/", True),
        WorldSource("South Asia", "Kabul Now",
                    "https://kabulnow.com/feed/"),
    ],
    "Oceania": [
        WorldSource("Oceania", "ABC News AU",
                    "https://www.abc.net.au/news/feed/51120/rss.xml"),
        WorldSource("Oceania", "RNZ",
                    "https://www.rnz.co.nz/rss/world.xml"),
        WorldSource("Oceania", "ABC News AU World",
                    "https://www.abc.net.au/news/feed/2942460/rss.xml"),
        WorldSource("Oceania", "ABC News AU Business",
                    "https://www.abc.net.au/news/feed/51892/rss.xml"),
        WorldSource("Oceania", "Sydney Morning Herald",
                    "https://www.smh.com.au/rss/feed.xml"),
        WorldSource("Oceania", "The Age",
                    "https://www.theage.com.au/rss/feed.xml"),
        WorldSource("Oceania", "SBS News",
                    "https://www.sbs.com.au/news/feed"),
        WorldSource("Oceania", "The Guardian Australia",
                    "https://www.theguardian.com/au/rss"),
        WorldSource("Oceania", "RNZ Pacific",
                    "https://www.rnz.co.nz/rss/pacific.xml"),
        WorldSource("Oceania", "Stuff NZ",
                    "https://www.stuff.co.nz/rss"),
        WorldSource("Oceania", "Papua New Guinea Post",
                    "https://www.postcourier.com.pg/feed/"),
    ],
    "Global": [
        WorldSource("Global", "UN News",
                    "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
        WorldSource("Global", "WHO News",
                    "https://www.who.int/rss-feeds/news-english.xml", True),
        WorldSource("Global", "Human Rights Watch",
                    "https://www.hrw.org/rss/news"),
        WorldSource("Global", "Amnesty International",
                    "https://www.amnesty.org/en/rss/", True),
        WorldSource("Global", "European Commission",
                    "https://ec.europa.eu/commission/presscorner/api/rss"),
        WorldSource("Global", "Reuters World (Google)",
                    "https://news.google.com/rss/search?q=when:24h+site:reuters.com+world&hl=en-US&gl=US&ceid=US:en"),
        WorldSource("Global", "Guardian Global",
                    "https://www.theguardian.com/international/rss"),
        WorldSource("Global", "Carbon Brief",
                    "https://www.carbonbrief.org/feed", True),
        WorldSource("Global", "Climate Home News",
                    "https://www.climatechangenews.com/feed/", True),
        WorldSource("Global", "Grist",
                    "https://grist.org/feed/", True),
        WorldSource("Global", "Inside Climate News",
                    "https://insideclimatenews.org/feed/", True),
        WorldSource("Global", "FreightWaves",
                    "https://www.freightwaves.com/feed", True),
        WorldSource("Global", "SemiAnalysis",
                    "https://www.semianalysis.com/feed", True),
        WorldSource("Global", "PassBlue",
                    "https://www.passblue.com/feed/", True),
    ],
}

def all_feeds() -> list[WorldSource]:
    """Every world feed, flattened (registry order, not rotation order)."""
    return [s for feeds in WORLD_FEEDS.values() for s in feeds]


def regions() -> list[str]:
    return list(WORLD_FEEDS.keys())


def feeds_for_region(region: str) -> list[WorldSource]:
    return WORLD_FEEDS.get(region, [])


def full_text_feeds() -> list[WorldSource]:
    """Feeds that ship full article text — best for multi-fact longform."""
    return [s for s in all_feeds() if s.full_text]


def region_of(url: str) -> str:
    """The region a feed URL belongs to, or "" when it is not a world feed.

    The world registry is the source of truth for region membership. A user's
    own added feed is not in it and has no region — which is correct: it is
    not part of the curated world coverage, and rotation treats it separately.
    """
    for region, feeds in WORLD_FEEDS.items():
        for feed in feeds:
            if feed.url == url:
                return region
    return ""


def rotation_order(urls: list[str]) -> list[str]:
    """Order feed URLs so consecutive cycles come from different regions.

    The world registry holds far more North American feeds than Oceanian ones.
    In registry order a listener would hear ~48 North American cycles in a row
    before another region appeared.

    Regions are interleaved by *proportional position*: the j-th feed of a
    region with n feeds sits at (j + 0.5) / n, and the whole set is sorted by
    that key. Each region is therefore spread evenly across the rotation, and a
    region's share of the sequence equals its share of feeds.

    A naive round-by-round interleave is not enough: it interleaves correctly
    while every region still has a feed left in that round, then dumps the
    largest region's surplus at the end (with 49 North American feeds against
    38 European, the last 11 slots were all North America). Proportional
    positions have no tail — the surplus is spread through the whole order.

    Over a long run each region's share of airtime still follows its share of
    feeds: a region with more outlets genuinely has more to report, and giving
    Oceania the same airtime as North America would misrepresent the world as
    much as the reverse.

    Feeds that are not in the world registry (the user's own additions) keep
    their relative order at the front, since they were asked for explicitly.
    Unknown URLs are never dropped.
    """
    by_region: dict[str, list[str]] = {}
    ordered_regions: list[str] = []
    extras: list[str] = []
    seen = set()

    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        region = region_of(url)
        if not region:
            extras.append(url)
            continue
        if region not in by_region:
            by_region[region] = []
            ordered_regions.append(region)
        by_region[region].append(url)

    keyed = []
    for region_index, region in enumerate(ordered_regions):
        feeds = by_region[region]
        count = len(feeds)
        for position, url in enumerate(feeds):
            # The region index breaks ties so the order is deterministic.
            keyed.append(((position + 0.5) / count, region_index, url))

    keyed.sort()
    return extras + [url for _, _, url in keyed]


# Convenience tuples for callers that just want (region, name, url).
def as_tuples() -> list[tuple[str, str, str]]:
    """[(region, name, url), ...] for every feed."""
    return [(s.region, s.name, s.url) for s in all_feeds()]
