"""Fetch and extract readable text from the client's website for F3.

Landing page plus a few company-relevant subpages (Über uns / Unternehmen /
Produkte / Leistungen / Verfahren), main-content extracted via trafilatura and
concatenated with a hard length cap. Best-effort: unreachable pages are skipped.
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura

_UA = "Invoice-Controller/0.1 (BAFA EEW tooling; contact tinonikandros@gmail.com)"
_SUBPAGE_HINTS = (
    "ueber-uns", "uber-uns", "über-uns", "ueberuns", "about", "unternehmen",
    "profil", "company", "produkte", "produkt", "leistungen", "leistung",
    "verfahren", "technologie", "fertigung", "produktion", "service",
)
_MAX_PAGES = 3            # landing + 2 subpages
_REQUEST_TIMEOUT = 10.0
_TIME_BUDGET_S = 35.0     # stop following subpages once the wall-clock budget is spent
_MAX_CHARS = 16000
_MIN_MEANINGFUL = 50      # fewer chars than this = an empty page or a JS "Loading…" shell
# Many corporate sites 404 the bare root or serve a client-rendered shell there, but a
# locale subpath has the real content — try these when the root yields nothing usable.
_LOCALE_PATHS = ("/de/", "/de", "/en/", "/en")


def _norm_url(url: str) -> str:
    if not urlparse(url).scheme:
        url = "https://" + url
    return url


def _fetch(url: str, client: httpx.Client) -> str | None:
    try:
        r = client.get(url, timeout=_REQUEST_TIMEOUT, follow_redirects=True,
                        headers={"User-Agent": _UA})
        if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
            return r.text
    except Exception:
        return None
    return None


def _links(html: str, base: str) -> list[str]:
    base_host = urlparse(base).netloc
    found: list[str] = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.IGNORECASE):
        low = href.lower()
        if not any(h in low for h in _SUBPAGE_HINTS):
            continue
        absu = urljoin(base, href)
        if urlparse(absu).netloc == base_host and absu not in found:
            found.append(absu)
    return found


def _extract(html: str) -> str:
    return trafilatura.extract(html, include_comments=False, include_tables=False) or ""


def _fetch_landing(url: str, client: httpx.Client) -> tuple[str, str, str]:
    """First candidate URL that yields meaningful text → (html, effective_url, main_text).

    Tries the given URL, then locale subpaths — so a root that 404s or renders a JS shell
    still resolves to the site's real content.
    """
    base = url.rstrip("/")
    for cand in (url, *(base + p for p in _LOCALE_PATHS)):
        html = _fetch(cand, client)
        if not html:
            continue
        main = _extract(html)
        if len(main) >= _MIN_MEANINGFUL:
            return html, cand, main
    return "", url, ""


def scrape_site_text(url: str, *, client: httpx.Client | None = None) -> str:
    """Return concatenated readable text from the site (landing + key subpages)."""
    url = _norm_url(url)
    own_client = client is None
    client = client or httpx.Client()
    try:
        landing, landing_url, main = _fetch_landing(url, client)
        if not landing:
            return ""
        texts: list[str] = [main]
        deadline = time.monotonic() + _TIME_BUDGET_S
        for sub in _links(landing, landing_url)[: _MAX_PAGES - 1]:
            if time.monotonic() > deadline:
                break
            html = _fetch(sub, client)
            if not html:
                continue
            t = _extract(html)
            if t:
                texts.append(t)
        return "\n\n".join(texts)[:_MAX_CHARS]
    finally:
        if own_client:
            client.close()
