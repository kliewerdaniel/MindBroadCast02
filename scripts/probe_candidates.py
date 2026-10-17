#!/usr/bin/env python3
"""Probe a large candidate list of RSS feeds; print the live ones.

Companion to scripts/probe_feeds.py, for expanding the world registry. Prints
a ready-to-paste block of WorldSource(...) lines for every candidate that
returns a parseable feed with entries, so nothing unverified reaches the
registry.

    python3 scripts/probe_candidates.py            # probe + report
    python3 scripts/probe_candidates.py --emit     # emit paste-ready lines
"""

import concurrent.futures as cf
import sys

import feedparser
import urllib.request

# (region, name, url). Every entry is a CANDIDATE: it is only trusted after
# this script says it is live. Kept deliberately broad so a region with a dead
# outlet still has live alternatives to fall back on.
CANDIDATES = [
    # ── North America ────────────────────────────────────────────────
    ("North America", "Reuters (via Google)", "https://news.google.com/rss/search?q=when:24h+site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
    ("North America", "AP Top News", "https://apnews.com/hub/ap-top-news/feed"),
    ("North America", "NYT World", "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
    ("North America", "NYT US", "https://rss.nytimes.com/services/xml/rss/nyt/US.xml"),
    ("North America", "NYT Business", "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml"),
    ("North America", "NYT Technology", "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml"),
    ("North America", "Washington Post World", "https://feeds.washingtonpost.com/rss/world"),
    ("North America", "Washington Post Politics", "https://feeds.washingtonpost.com/rss/politics"),
    ("North America", "CNN Top Stories", "https://rss.cnn.com/rss/edition.rss"),
    ("North America", "CNN World", "https://rss.cnn.com/rss/edition_world.rss"),
    ("North America", "NPR Politics", "https://feeds.npr.org/1014/rss.xml"),
    ("North America", "NPR Business", "https://feeds.npr.org/1006/rss.xml"),
    ("North America", "NPR Technology", "https://feeds.npr.org/1019/rss.xml"),
    ("North America", "NPR Science", "https://feeds.npr.org/1007/rss.xml"),
    ("North America", "NPR Health", "https://feeds.npr.org/1128/rss.xml"),
    ("North America", "PBS NewsHour", "https://www.pbs.org/newshour/feeds/rss/headlines"),
    ("North America", "ABC News (US)", "https://abcnews.go.com/abcnews/topstories"),
    ("North America", "CBS News", "https://www.cbsnews.com/latest/rss/main"),
    ("North America", "NBC News", "https://feeds.nbcnews.com/nbcnews/public/news"),
    ("North America", "Politico", "https://rss.politico.com/politics-news.xml"),
    ("North America", "The Hill", "https://thehill.com/feed/"),
    ("North America", "Axios", "https://api.axios.com/feed/"),
    ("North America", "ProPublica", "https://www.propublica.org/feeds/propublica/main"),
    ("North America", "The Atlantic", "https://www.theatlantic.com/feed/all/"),
    ("North America", "Foreign Policy", "https://foreignpolicy.com/feed/"),
    ("North America", "CBC World", "https://www.cbc.ca/webfeed/rss/rss-world"),
    ("North America", "CBC Top Stories", "https://www.cbc.ca/webfeed/rss/rss-topstories"),
    ("North America", "Global News CA", "https://globalnews.ca/feed/"),
    ("North America", "Toronto Star", "https://www.thestar.com/rss/news.html"),
    ("North America", "The Globe and Mail", "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/"),
    ("North America", "Mexico News Daily", "https://mexiconewsdaily.com/feed/"),
    ("North America", "Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("North America", "The Verge", "https://www.theverge.com/rss/index.xml"),
    ("North America", "Wired", "https://www.wired.com/feed/rss"),
    ("North America", "MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("North America", "Science Daily", "https://www.sciencedaily.com/rss/all.xml"),
    ("North America", "Nature News", "https://www.nature.com/nature.rss"),
    ("North America", "NASA Breaking News", "https://www.nasa.gov/rss/dyn/breaking_news.rss"),
    ("North America", "STAT News", "https://www.statnews.com/feed/"),
    ("North America", "KFF Health News", "https://kffhealthnews.org/feed/"),
    ("North America", "Politico EU", "https://www.politico.eu/feed/"),
    ("North America", "CFR", "https://www.cfr.org/rss-feeds/blog"),
    ("North America", "Brookings", "https://www.brookings.edu/feed/"),
    ("North America", "Lawfare", "https://www.lawfaremedia.org/feeds/all"),
    ("North America", "Bellingcat", "https://www.bellingcat.com/feed/"),
    ("North America", "The Intercept", "https://theintercept.com/feed/?rss"),
    ("North America", "Vox", "https://www.vox.com/rss/index.xml"),
    ("North America", "Slate", "https://slate.com/feeds/all.rss"),
    ("North America", "The Conversation", "https://theconversation.com/articles.atom"),
    ("North America", "MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("North America", "CNBC World", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100727362"),
    ("North America", "Federal Reserve", "https://www.federalreserve.gov/feeds/press_all.xml"),

    # ── Europe ───────────────────────────────────────────────────────
    ("Europe", "BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
    ("Europe", "BBC Technology", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("Europe", "BBC Science", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"),
    ("Europe", "BBC Health", "https://feeds.bbci.co.uk/news/health/rss.xml"),
    ("Europe", "BBC Politics", "https://feeds.bbci.co.uk/news/politics/rss.xml"),
    ("Europe", "BBC UK", "https://feeds.bbci.co.uk/news/uk/rss.xml"),
    ("Europe", "The Guardian UK", "https://www.theguardian.com/uk-news/rss"),
    ("Europe", "The Guardian Business", "https://www.theguardian.com/uk/business/rss"),
    ("Europe", "The Guardian Technology", "https://www.theguardian.com/uk/technology/rss"),
    ("Europe", "The Guardian Environment", "https://www.theguardian.com/environment/rss"),
    ("Europe", "Financial Times", "https://www.ft.com/rss/home"),
    ("Europe", "The Economist", "https://www.economist.com/the-world-this-week/rss.xml"),
    ("Europe", "The Independent", "https://www.independent.co.uk/news/world/rss"),
    ("Europe", "Sky News", "https://feeds.skynews.com/feeds/rss/world.xml"),
    ("Europe", "Sky News UK", "https://feeds.skynews.com/feeds/rss/uk.xml"),
    ("Europe", "The Telegraph World", "https://www.telegraph.co.uk/world-news/rss.xml"),
    ("Europe", "France 24", "https://www.france24.com/en/rss"),
    ("Europe", "RFI English", "https://www.rfi.fr/en/rss"),
    ("Europe", "Der Spiegel EN", "https://www.spiegel.de/international/index.rss"),
    ("Europe", "Deutsche Welle Europe", "https://rss.dw.com/rdf/rss-en-eu"),
    ("Europe", "Deutsche Welle Germany", "https://rss.dw.com/rdf/rss-en-ger"),
    ("Europe", "Politico Europe", "https://www.politico.eu/feed/"),
    ("Europe", "EUobserver", "https://euobserver.com/rss"),
    ("Europe", "Eurostat News", "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/rss/en/news"),
    ("Europe", "El Pais English", "https://feeds.elpais.com/mrss-s/pages/ep/site/english.elpais.com/portada"),
    ("Europe", "Le Figaro EN", "https://www.lefigaro.fr/rss/figaro_actualites.xml"),
    ("Europe", "ANSA English", "https://www.ansa.it/english/news/english_rss.xml"),
    ("Europe", "Dutch News", "https://www.dutchnews.nl/feed/"),
    ("Europe", "The Local Germany", "https://www.thelocal.de/feeds/rss.php"),
    ("Europe", "Notes from Poland", "https://notesfrompoland.com/feed/"),
    ("Europe", "Kyiv Independent", "https://kyivindependent.com/feed/"),
    ("Europe", "Ukrinform", "https://www.ukrinform.net/rss/block-lastnews"),
    ("Europe", "Reuters Europe (Google)", "https://news.google.com/rss/search?q=when:24h+site:reuters.com+europe&hl=en-US&gl=US&ceid=US:en"),
    ("Europe", "Irish Times", "https://www.irishtimes.com/arc/outboundfeeds/rss/"),
    ("Europe", "RTÉ News", "https://www.rte.ie/feeds/rss/?index=/news/"),
    ("Europe", "Swissinfo", "https://www.swissinfo.ch/eng/rss"),
    ("Europe", "The Nordic Page", "https://www.tnp.no/feed"),
    ("Europe", "Yle News", "https://feeds.yle.fi/uutiset/v1/majorHeadlines/YLE_NEWS.rss"),
    ("Europe", "The Moscow Times", "https://www.themoscowtimes.com/rss/news"),

    # ── Middle East ──────────────────────────────────────────────────
    ("Middle East", "Al Jazeera Middle East", "https://www.aljazeera.com/xml/rss/all.xml"),
    ("Middle East", "Al Arabiya English", "https://english.alarabiya.net/tools/rss"),
    ("Middle East", "The National (UAE)", "https://www.thenationalnews.com/arc/outboundfeeds/rss/"),
    ("Middle East", "Gulf News", "https://gulfnews.com/rss"),
    ("Middle East", "Middle East Eye", "https://www.middleeasteye.net/rss"),
    ("Middle East", "Haaretz", "https://www.haaretz.com/srv/htz---.xml"),
    ("Middle East", "The Jerusalem Post", "https://www.jpost.com/rss/rssfeedsfrontpage.aspx"),
    ("Middle East", "Al-Monitor", "https://www.al-monitor.com/rss"),
    ("Middle East", "Iran International", "https://www.iranintl.com/en/rss"),
    ("Middle East", "Rudaw", "https://www.rudaw.net/english/rss"),
    ("Middle East", "Daily Sabah", "https://www.dailysabah.com/rss"),
    ("Middle East", "Hürriyet Daily News", "https://www.hurriyetdailynews.com/rss"),
    ("Middle East", "Jordan Times", "https://jordantimes.com/rss"),
    ("Middle East", "L'Orient Today", "https://today.lorientlejour.com/rss"),
    ("Middle East", "Egypt Independent", "https://egyptindependent.com/feed/"),
    ("Middle East", "Kuwait Times", "https://kuwaittimes.com/feed/"),
    ("Middle East", "Bahrain News Agency", "https://www.bna.bh/en/rss.aspx"),
    ("Middle East", "Times of Oman", "https://timesofoman.com/rss"),

    # ── Asia ─────────────────────────────────────────────────────────
    ("Asia", "Nikkei Asia", "https://asia.nikkei.com/rss/feed/nar"),
    ("Asia", "South China Morning Post", "https://www.scmp.com/rss/91/feed"),
    ("Asia", "Channel News Asia", "https://www.channelnewsasia.com/api/v1/rss-outbound-feed?_format=xml"),
    ("Asia", "The Straits Times Asia", "https://www.straitstimes.com/news/asia/rss.xml"),
    ("Asia", "Korea Herald", "https://www.koreaherald.com/rss/newsAll"),
    ("Asia", "The Korea Times", "https://www.koreatimes.co.kr/www/rss/rss.xml"),
    ("Asia", "Yonhap", "https://en.yna.co.kr/RSS/news.xml"),
    ("Asia", "Global Times", "https://www.globaltimes.cn/rss/outbrain.xml"),
    ("Asia", "China Daily", "https://www.chinadaily.com.cn/rss/world_rss.xml"),
    ("Asia", "Taipei Times", "https://www.taipeitimes.com/xml/index.rss"),
    ("Asia", "Focus Taiwan", "https://focustaiwan.tw/rss/news.xml"),
    ("Asia", "Bangkok Post", "https://www.bangkokpost.com/rss/data/topstories.xml"),
    ("Asia", "The Jakarta Post", "https://www.thejakartapost.com/index.rss"),
    ("Asia", "Jakarta Globe", "https://jakartaglobe.id/feed"),
    ("Asia", "Rappler", "https://www.rappler.com/feed/"),
    ("Asia", "Inquirer", "https://newsinfo.inquirer.net/feed"),
    ("Asia", "Vietnam News", "https://vietnamnews.vn/rss/home.rss"),
    ("Asia", "VnExpress International", "https://e.vnexpress.net/rss/news.rss"),
    ("Asia", "The Diplomat", "https://thediplomat.com/feed/"),
    ("Asia", "Malay Mail", "https://www.malaymail.com/feed/rss/malaysia"),
    ("Asia", "Asia Times", "https://asiatimes.com/feed/"),
    ("Asia", "The Irrawaddy", "https://www.irrawaddy.com/feed"),
    ("Asia", "Benar News", "https://www.benarnews.org/english/rss.xml"),
    ("Asia", "Mongolia Weekly", "https://www.mongoliaweekly.org/feed"),
    ("Asia", "Nepali Times", "https://nepalitimes.com/rss"),
    ("Asia", "The Kathmandu Post", "https://kathmandupost.com/rss"),
    ("Asia", "Kuensel (Bhutan)", "https://kuenselonline.com/feed/"),
    ("Asia", "The Japan News", "https://japannews.yomiuri.co.jp/feed/"),
    ("Asia", "Japan Today", "https://japantoday.com/feed"),

    # ── Africa ───────────────────────────────────────────────────────
    ("Africa", "News24 Africa", "https://feeds.news24.com/news24/Africa"),
    ("Africa", "News24 South Africa", "https://feeds.news24.com/news24/SouthAfrica"),
    ("Africa", "Mail & Guardian", "https://mg.co.za/feed/"),
    ("Africa", "Daily Maverick", "https://www.dailymaverick.co.za/feed/"),
    ("Africa", "The Africa Report", "https://www.theafricareport.com/feed/"),
    ("Africa", "African Business", "https://african.business/feed"),
    ("Africa", "African Arguments", "https://africanarguments.org/feed/"),
    ("Africa", "The EastAfrican", "https://www.theeastafrican.co.ke/tea/rss"),
    ("Africa", "Business Daily Africa", "https://www.businessdailyafrica.com/bd/rss"),
    ("Africa", "Daily Nation Kenya", "https://nation.africa/kenya/rss"),
    ("Africa", "The Standard Kenya", "https://www.standardmedia.co.ke/rss/headlines.php"),
    ("Africa", "The Citizen Tanzania", "https://www.thecitizen.co.tz/tanzania/rss"),
    ("Africa", "New Vision Uganda", "https://www.newvision.co.ug/rss"),
    ("Africa", "The New Times Rwanda", "https://www.newtimes.co.rw/rss.xml"),
    ("Africa", "Addis Standard", "https://addisstandard.com/feed/"),
    ("Africa", "Sudan Tribune", "https://sudantribune.com/feed/"),
    ("Africa", "Vanguard Nigeria", "https://www.vanguardngr.com/feed/"),
    ("Africa", "Premium Times", "https://www.premiumtimesng.com/feed"),
    ("Africa", "The Punch", "https://punchng.com/feed/"),
    ("Africa", "GhanaWeb", "https://www.ghanaweb.com/GhanaHomePage/rss/news.xml"),
    ("Africa", "Graphic Online", "https://www.graphic.com.gh/feed.html"),
    ("Africa", "Morocco World News", "https://www.moroccoworldnews.com/feed/"),
    ("Africa", "Libya Observer", "https://libyaobserver.ly/rss.xml"),
    ("Africa", "Zimbabwe Situation", "https://www.zimbabwesituation.com/feed/"),
    ("Africa", "The Herald Zimbabwe", "https://www.herald.co.zw/feed/"),
    ("Africa", "Mozambique (AIM)", "https://www.aimnews.org.mz/feed"),
    ("Africa", "Seychelles News Agency", "https://www.seychellesnewsagency.com/rss.xml"),

    # ── Latin America ────────────────────────────────────────────────
    ("Latin America", "Buenos Aires Times", "https://www.batimes.com.ar/feed"),
    ("Latin America", "Buenos Aires Herald", "https://buenosairesherald.com/feed"),
    ("Latin America", "MercoPress", "https://en.mercopress.com/rss/rss.xml"),
    ("Latin America", "Folha de S.Paulo", "https://feeds.folha.uol.com.br/internacional/en/rss091.xml"),
    ("Latin America", "Agência Brasil", "https://agenciabrasil.ebc.com.br/en/rss.xml"),
    ("Latin America", "Rio Times", "https://riotimesonline.com/feed/"),
    ("Latin America", "El Universal (Mexico)", "https://www.eluniversal.com.mx/rss.xml"),
    ("Latin America", "Reforma", "https://www.reforma.com/rss/portada.xml"),
    ("Latin America", "El Tiempo Colombia", "https://www.eltiempo.com/rss/colombia.xml"),
    ("Latin America", "Colombia Reports", "https://colombiareports.com/feed/"),
    ("Latin America", "El Comercio Peru", "https://elcomercio.pe/arcio/rss/"),
    ("Latin America", "La República Peru", "https://larepublica.pe/rss/"),
    ("Latin America", "La Tercera Chile", "https://www.latercera.com/arc/outboundfeeds/rss/"),
    ("Latin America", "Cooperativa Chile", "https://www.cooperativa.cl/noticias/site/tax/port/all/rss_1.xml"),
    ("Latin America", "El Observador Uruguay", "https://www.elobservador.com.uy/rss"),
    ("Latin America", "ABC Color Paraguay", "https://www.abc.com.py/arc/outboundfeeds/rss/"),
    ("Latin America", "El Deber Bolivia", "https://eldeber.com.bo/rss"),
    ("Latin America", "Primicias Ecuador", "https://www.primicias.ec/feed/"),
    ("Latin America", "Havana Times", "https://havanatimes.org/feed/"),
    ("Latin America", "14ymedio", "https://www.14ymedio.com/rss/"),
    ("Latin America", "Dominican Today", "https://dominicantoday.com/feed/"),
    ("Latin America", "Jamaica Gleaner", "https://jamaica-gleaner.com/feed/rss.xml"),
    ("Latin America", "Caribbean News Global", "https://www.caribbeannewsglobal.com/feed/"),
    ("Latin America", "Trinidad Guardian", "https://guardian.co.tt/rss.xml"),

    # ── South Asia ───────────────────────────────────────────────────
    ("South Asia", "The Hindu National", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("South Asia", "Times of India", "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"),
    ("South Asia", "Hindustan Times", "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"),
    ("South Asia", "Indian Express", "https://indianexpress.com/feed/"),
    ("South Asia", "The Wire India", "https://thewire.in/rss"),
    ("South Asia", "Scroll.in", "https://scroll.in/feed"),
    ("South Asia", "The Quint", "https://www.thequint.com/feeds/all.rss"),
    ("South Asia", "NDTV", "https://feeds.feedburner.com/ndtvnews-top-stories"),
    ("South Asia", "Business Standard", "https://www.business-standard.com/rss/home.rss"),
    ("South Asia", "The Economic Times", "https://economictimes.indiatimes.com/rssfeedstopstories.cms"),
    ("South Asia", "Livemint", "https://www.livemint.com/rss/news"),
    ("South Asia", "Dawn Pakistan", "https://www.dawn.com/feeds/home"),
    ("South Asia", "The Express Tribune", "https://tribune.com.pk/feed/home"),
    ("South Asia", "Pakistan Today", "https://www.pakistantoday.com.pk/feed/"),
    ("South Asia", "Dhaka Tribune", "https://www.dhakatribune.com/feed"),
    ("South Asia", "The Daily Star BD", "https://www.thedailystar.net/frontpage/rss.xml"),
    ("South Asia", "The Island (Sri Lanka)", "https://island.lk/feed/"),
    ("South Asia", "Daily Mirror LK", "https://www.dailymirror.lk/RSS_Feeds/breaking-news"),
    ("South Asia", "The Himalayan Times", "https://thehimalayantimes.com/feed/"),
    ("South Asia", "Kabul Now", "https://kabulnow.com/feed/"),

    # ── Oceania ──────────────────────────────────────────────────────
    ("Oceania", "ABC News AU World", "https://www.abc.net.au/news/feed/2942460/rss.xml"),
    ("Oceania", "ABC News AU Business", "https://www.abc.net.au/news/feed/51892/rss.xml"),
    ("Oceania", "Sydney Morning Herald", "https://www.smh.com.au/rss/feed.xml"),
    ("Oceania", "The Age", "https://www.theage.com.au/rss/feed.xml"),
    ("Oceania", "The Australian", "https://www.theaustralian.com.au/feed"),
    ("Oceania", "SBS News", "https://www.sbs.com.au/news/feed"),
    ("Oceania", "9News", "https://www.9news.com.au/rss"),
    ("Oceania", "The Guardian Australia", "https://www.theguardian.com/au/rss"),
    ("Oceania", "RNZ Pacific", "https://www.rnz.co.nz/rss/pacific.xml"),
    ("Oceania", "NZ Herald", "https://www.nzherald.co.nz/arc/outboundfeeds/rss/"),
    ("Oceania", "Stuff NZ", "https://www.stuff.co.nz/rss"),
    ("Oceania", "Fiji Times", "https://www.fijitimes.com.fj/feed/"),
    ("Oceania", "Pacific Islands News", "https://www.pina.com.fj/rss.php"),
    ("Oceania", "Papua New Guinea Post", "https://www.postcourier.com.pg/feed/"),

    # ── Global / institutions ────────────────────────────────────────
    ("Global", "UN News", "https://news.un.org/feed/subscribe/en/news/all/rss.xml"),
    ("Global", "WHO News", "https://www.who.int/rss-feeds/news-english.xml"),
    ("Global", "World Bank", "https://www.worldbank.org/en/news/all?format=rss"),
    ("Global", "IMF News", "https://www.imf.org/en/News/RSS"),
    ("Global", "ReliefWeb", "https://reliefweb.int/updates/rss.xml"),
    ("Global", "Human Rights Watch", "https://www.hrw.org/rss/news"),
    ("Global", "Amnesty International", "https://www.amnesty.org/en/rss/"),
    ("Global", "ICRC", "https://www.icrc.org/en/rss.xml"),
    ("Global", "IAEA", "https://www.iaea.org/feeds/topstories"),
    ("Global", "Interpol", "https://www.interpol.int/en/News-and-Events/News/rss"),
    ("Global", "European Commission", "https://ec.europa.eu/commission/presscorner/api/rss"),
    ("Global", "NATO", "https://www.nato.int/cps/en/natohq/news_rss.xml"),
    ("Global", "Reuters World (Google)", "https://news.google.com/rss/search?q=when:24h+site:reuters.com+world&hl=en-US&gl=US&ceid=US:en"),
    ("Global", "Guardian Global", "https://www.theguardian.com/international/rss"),
    ("Global", "Wikipedia Current Events", "https://en.wikipedia.org/w/api.php?action=featuredfeed&feed=currentevents&feedformat=atom"),
    ("Global", "Carbon Brief", "https://www.carbonbrief.org/feed"),
    ("Global", "Climate Home News", "https://www.climatechangenews.com/feed/"),
    ("Global", "Grist", "https://grist.org/feed/"),
    ("Global", "Inside Climate News", "https://insideclimatenews.org/feed/"),
    ("Global", "FreightWaves", "https://www.freightwaves.com/feed"),
    ("Global", "SemiAnalysis", "https://www.semianalysis.com/feed"),
    ("Global", "OCCRP", "https://www.occrp.org/en/rss"),
    ("Global", "Global Witness", "https://www.globalwitness.org/en/rss/"),
    ("Global", "PassBlue", "https://www.passblue.com/feed/"),
    ("Global", "Dever", "https://devex.com/news/rss"),
]


def probe(item):
    region, name, url = item
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (MindBroadcast probe)"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            body = resp.read()
        feed = feedparser.parse(body)
        entries = feed.entries
        n = len(entries)
        has_full = any(
            len((e.get("content") or [{}])[0].get("value", "")) > 400
            for e in entries[:5])
        return (region, name, url, n, has_full, "OK")
    except Exception as exc:
        return (region, name, url, 0, False,
                f"{type(exc).__name__}: {str(exc)[:60]}")


def main() -> int:
    emit = "--emit" in sys.argv
    as_json = "--json" in sys.argv
    results = []
    with cf.ThreadPoolExecutor(max_workers=16) as pool:
        for r in pool.map(probe, CANDIDATES):
            results.append(r)

    live = [r for r in results if r[3] > 0]
    dead = [r for r in results if r[3] == 0]

    if as_json:
        # Structured output, so a caller never has to parse fixed-width text.
        import json
        print(json.dumps({
            "live": [
                {"region": region, "name": name, "url": url,
                 "entries": n, "full_text": full}
                for region, name, url, n, full, _ in live
            ],
            "dead": [
                {"region": region, "name": name, "url": url, "error": status}
                for region, name, url, _, _, status in dead
            ],
        }, indent=2))
        return 0 if live else 1

    for region, name, url, n, full, status in sorted(results):
        flag = "FULL" if full else ("ok  " if n else "DEAD")
        print(f"  [{flag}] {region:15} {name:28} entries={n:4}  {url}  {status}")

    print(f"\n{len(live)}/{len(results)} live")

    if dead:
        print("\nDEAD (do not add):")
        for region, name, url, _, _, status in dead:
            print(f"  {name:28} {status}")

    if emit:
        print("\n# ── paste-ready ──")
        current = None
        for region, name, url, n, full, _ in sorted(live):
            if region != current:
                print(f'    # {region}')
                current = region
            suffix = ", True" if full else ""
            print(f'        WorldSource("{region}", "{name}",\n'
                  f'                    "{url}"{suffix}),')
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())
