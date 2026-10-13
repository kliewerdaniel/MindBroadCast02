"""Article body enrichment.

The RSS feed gives a one-line summary per entry, which the compiler turns into
a single claim — that is the root cause of one-sentence, sparse broadcasts. To
compose longform, an article must carry real prose. This module fetches the
article at its link and extracts the readable body text.

Boundaries:
- Fail-soft: any failure (timeout, 404, paywall, redirect guard, parse error)
  returns None, and the caller falls back to the RSS summary. Enrichment never
  blocks ingestion (§23.3: work is bounded; a source that won't yield a body
  is simply captured from its summary).
- Readability over cleverness: strip scripts/styles, drop the shortest noise,
  and keep the densest text block. Good enough to give the compiler sentences;
  not a full Readability.js port.
"""

from typing import Optional


def extract_body_text(html: str, min_chars: int = 400) -> Optional[str]:
    """Extract readable article text from HTML, or None if too thin.

    Prefers semantic article containers, then falls back to the densest
    paragraph block. Returns None when nothing substantial is found so the
    caller keeps the RSS summary rather than storing boilerplate.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return None

    for tag in soup(["script", "style", "noscript", "svg", "form",
                     "nav", "header", "footer", "aside"]):
        tag.decompose()

    # Prefer a semantic article region if the page provides one.
    container = (soup.find("article")
                 or soup.find("main")
                 or soup.find(attrs={"role": "main"}))
    scope = container if container is not None else soup

    paragraphs = [
        p.get_text(" ", strip=True)
        for p in scope.find_all("p")
    ]
    paragraphs = [p for p in paragraphs if len(p) >= 40]

    if not paragraphs:
        return None

    text = "\n".join(paragraphs).strip()
    if len(text) < min_chars:
        return None
    return text


async def enrich_with_body(fetcher: object, url: str,
                           min_chars: int = 400) -> Optional[str]:
    """Fetch `url` and return its readable body text, or None on any failure.

    Fail-soft by contract: the caller decides what to do with None. Never
    raises out of here — an enrichment failure degrades to the RSS summary.
    """
    if not url:
        return None
    try:
        result = await fetcher.fetch(url)
    except Exception:
        return None
    body = getattr(result, "body", "") or ""
    if not body:
        return None
    return extract_body_text(body, min_chars=min_chars)
