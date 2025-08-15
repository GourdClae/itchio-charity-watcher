# itchio_charity_bundles_feed.py
# Builds an RSS feed (feed.xml) of likely charity bundle/jam opportunities on itch.io.
# Sources:
#   - Blog index (charity keywords; date-gated)                    -> [BLOG]
#   - Game Jams board (follows thread pages 1 click deep; date)    -> [BOARD]
#   - Jams "Starting This Month/Week/In Progress" (paginated)      -> [JAMS]
#       Paginates ?page=2,3,..., de-dupes across lists & sorts, and
#       opens each jam page to scan full description for charity terms.
#
# Charity-only matching (no "submit" keyword required).
# Fresh-only filter for BLOG/BOARD items and date-aware pubDate.

import re, json, time, hashlib, datetime as dt
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

import requests
from bs4 import BeautifulSoup as BS
from xml.etree.ElementTree import Element, SubElement, ElementTree

USER_AGENT = "itchio-charity-watcher/1.9"
OUT_FEED = Path("feed.xml")
STATE = Path(".seen.json")

# --- Settings ----------------------------------------------------------------
# Non-jam (blog/threads) freshness window:
MAX_AGE_DAYS = 30
# How many pages of jam listings to crawl per base URL:
MAX_JAMS_PAGES = 5          # increase if you want deeper crawl
MAX_JAMS_PER_PAGE = 60      # safety cap per page
MAX_JAMS_TOTAL = 400        # overall safety cap per run across all jam lists

# --- Keyword filters (charity only) ------------------------------------------
CHARITY = re.compile(
    r"\b(charity|fundraiser|donation|relief|mutual aid|non[- ]?profit|benefit|fund(?:\s*raiser)?)\b",
    re.I,
)

# --- Sources -----------------------------------------------------------------
SOURCES = [
    ("https://itch.io/blog", "[BLOG]"),
    ("https://itch.io/board/533649/game-jams", "[BOARD]"),                    # Game Jams board
    # Month views (default + sort-by-date)
    ("https://itch.io/jams/starting-this-month", "[JAMS]"),
    ("https://itch.io/jams/starting-this-month/sort-date", "[JAMS]"),
    # Week views (default + sort-by-date)
    ("https://itch.io/jams/starting-this-week", "[JAMS]"),
    ("https://itch.io/jams/starting-this-week/sort-date", "[JAMS]"),
    # In-progress views (default + sort-by-date)
    ("https://itch.io/jams/in-progress", "[JAMS]"),
    ("https://itch.io/jams/in-progress/sort-date", "[JAMS]"),
]

# Global de-dupe for jam links across all lists
JAMS_SEEN_LINKS = set()

# --- HTTP helpers -------------------------------------------------------------
def get(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text

def to_abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return "https://itch.io" + href
    return urljoin("https://itch.io/", href)

# --- Time helpers -------------------------------------------------------------
def parse_iso_any(ts: str):
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return dt.datetime.fromisoformat(ts)
    except Exception:
        return None

def find_page_timestamp(soup: BS):
    """Try to find a meaningful published/updated time on blog/thread pages."""
    for t in soup.select("time[datetime]"):
        d = parse_iso_any(t.get("datetime", "").strip())
        if d:
            return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    for sel in [
        "meta[property='article:published_time']",
        "meta[name='date']",
        "meta[name='pubdate']",
        "meta[itemprop='datePublished']",
        "meta[itemprop='dateModified']",
    ]:
        m = soup.select_one(sel)
        if m and m.get("content"):
            d = parse_iso_any(m["content"].strip())
            if d:
                return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    return None

def within_age(ts, days: int = MAX_AGE_DAYS) -> bool:
    if ts is None:
        return False
    now = dt.datetime.now(dt.timezone.utc)
    return ts >= now - dt.timedelta(days=days)

# --- Jam listing parsing (exclude 'Ended …' AND follow jam pages) ------------
# Include a few status phrases we might see on cards
JAM_STATUS_HINT = re.compile(r"(Starts in|Submission closes in|Ends in|Closes in|Ended)", re.I)

def parse_iso(ts: str):
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return dt.datetime.fromisoformat(ts)
    except Exception:
        return None

def extract_text(elem) -> str:
    return " ".join((elem.get_text(" ") if elem else "").split())

def jam_page_matches(full_html: str):
    """Check full jam page for charity keywords; return (match, summary_text, soup)."""
    soup = BS(full_html, "html.parser")
    chunks = []
    for sel in [
        ".jam_summary", ".jam_header", ".jam_body", ".jam_about",
        ".formatted_description", ".user_formatted_description", "article"
    ]:
        for node in soup.select(sel):
            chunks.append(extract_text(node))
    if not chunks:
        chunks.append(extract_text(soup.body))
    text = " ".join(chunks)
    match = bool(CHARITY.search(text))
    return match, text[:280], soup

def set_page(url: str, page_num: int) -> str:
    """Return url with ?page=page_num (preserving existing query)."""
    pr = urlparse(url)
    q = parse_qs(pr.query)
    q["page"] = [str(page_num)]
    new_q = urlencode(q, doseq=True)
    return urlunparse((pr.scheme, pr.netloc, pr.path, pr.params, new_q, pr.fragment))

def collect_jam_links_from_listing(base_url: str, max_pages: int, per_page_cap: int, total_cap: int):
    """Iterate listing pages, collect unique jam links with basic card filtering."""
    now = dt.datetime.now(dt.timezone.utc)
    collected = []
    for p in range(1, max_pages + 1):
        if len(collected) >= total_cap:
            break
        page_url = base_url if p == 1 and "page=" not in base_url else set_page(base_url, p)
        try:
            html = get(page_url)
        except Exception as e:
            print("WARN listing:", page_url, e)
            continue
        soup = BS(html, "html.parser")

        seen_page = 0
        # Find jam cards via links to /jam/...
        for a in soup.select("a[href*='/jam/']"):
            link = to_abs(a.get("href") or "")
            if not link.startswith("https://itch.io/jam/"):
                continue
            if link in JAMS_SEEN_LINKS:
                continue

            # Try to find card container and status text
            anchor = a
            container = anchor
            for _ in range(3):
                if container and container.parent:
                    container = container.parent
            text_blob = extract_text(container) if container else (extract_text(anchor) if anchor else "")
            if re.search(r"\bEnded\b", text_blob, re.I):
                continue

            # Timestamp from card if any
            ts_val = None
            t = container.find("time") if container else None
            if t and t.has_attr("datetime"):
                ts_val = parse_iso(t["datetime"])
                if ts_val and ts_val.tzinfo is None:
                    ts_val = ts_val.replace(tzinfo=dt.timezone.utc)

            starts_in = bool(re.search(r"\bStarts in\b", text_blob, re.I))
            closes_in = bool(re.search(r"\bSubmission closes in\b", text_blob, re.I))
            ends_in   = bool(re.search(r"\b(Ends in|Closes in)\b", text_blob, re.I))

            keep = False
            if ts_val:
                if (starts_in or closes_in or ends_in) and ts_val > now:
                    keep = True
            else:
                if starts_in or closes_in or ends_in:
                    keep = True

            if not keep:
                continue

            JAMS_SEEN_LINKS.add(link)
            collected.append((link, ts_val))
            seen_page += 1
            if seen_page >= per_page_cap or len(collected) >= total_cap:
                break

        # If a page yields nothing, we can stop early
        if seen_page == 0 and p > 1:
            break

        time.sleep(1)  # polite delay between listing pages
    return collected

def items_from_jams_list(base_url: str, label: str):
    """Paginate through listing (month/week/in-progress), then follow each jam page and apply full-body checks."""
    out = []
    kept = collect_jam_links_from_listing(
        base_url, MAX_JAMS_PAGES, MAX_JAMS_PER_PAGE, MAX_JAMS_TOTAL
    )
    for jlink, card_ts in kept:
        try:
            jhtml = get(jlink)
            ok, snippet, jsoup = jam_page_matches(jhtml)
            if ok:
                title = extract_text(jsoup.select_one("h1, .jam_title, .header_title")) or "Jam"
                out.append({
                    "title": f"{label} {title}"[:160],
                    "link": jlink,
                    "summary": snippet,
                    "ts": card_ts or dt.datetime.now(dt.timezone.utc)
                })
            time.sleep(1)
        except Exception as e:
            print("WARN jam:", jlink, e)
    return out

# --- Generic HTML scanning with optional deep-follow for boards ---------------
THREAD_HREF = re.compile(r"/(community/\d+/\d+|board/\d+/[^/]+/.+)", re.I)

def items_from_html(url: str, html: str, label: str):
    soup = BS(html, "html.parser")
    candidates = []

    # Jam listings (month/week/in-progress) — paginated + deep jam scan
    if url.startswith((
        "https://itch.io/jams/starting-this-month",
        "https://itch.io/jams/starting-this-week",
        "https://itch.io/jams/in-progress"
    )):
        return items_from_jams_list(url, label)

    # Blog index — prefer real blog post links
    if url.rstrip("/") == "https://itch.io/blog":
        anchors = soup.select("a[href*='/blog/']") or soup.select("a")
        for a in anchors:
            href = to_abs(a.get("href") or "")
            text = extract_text(a)
            if not href or not text or not href.startswith("https://itch.io"):
                continue
            parent = a.find_parent()
            snippet = extract_text(parent)[:500] if parent else ""
            blob = f"{text} — {snippet}"
            if CHARITY.search(blob):
                ts = None
                try:
                    if href.startswith("https://itch.io/blog/"):
                        blog_html = get(href)
                        blog_soup = BS(blog_html, "html.parser")
                        ts = find_page_timestamp(blog_soup)
                except Exception:
                    ts = None
                if within_age(ts):
                    candidates.append({
                        "title": f"{label} {text}"[:160],
                        "link": href,
                        "summary": snippet[:280],
                        "ts": ts
                    })
        return candidates

    # Board listing — follow thread links one click deep
    if "/board/" in url and THREAD_HREF.search(url) is None:
        thread_links = []
        for a in soup.select("a[href*='/board/']"):
            href = a.get("href") or ""
            if THREAD_HREF.search(href or ""):
                thread_links.append(to_abs(href))
        for tlink in sorted(set(thread_links)):
            try:
                thtml = get(tlink)
                candidates.extend(items_from_html(tlink, thtml, label))
                time.sleep(1)  # polite
            except Exception as e:
                print("WARN thread:", tlink, e)
        return candidates

    # Generic page scan (thread pages land here) — charity-only + date-gate
    page_ts = find_page_timestamp(soup)
    for a in soup.select("a"):
        href = to_abs(a.get("href") or "")
        text = extract_text(a)
        if not href or not text or not href.startswith("https://itch.io"):
            continue
        parent = a.find_parent()
        snippet = extract_text(parent)[:500] if parent else ""
        blob = f"{text} — {snippet}"
        if CHARITY.search(blob) and within_age(page_ts):
            candidates.append({
                "title": f"{label} {text}"[:160],
                "link": href,
                "summary": snippet[:280],
                "ts": page_ts
            })
    return candidates

# --- State, RSS build, main ---------------------------------------------------
def hash_item(it) -> str:
    return hashlib.sha1((it["title"] + it["link"]).encode("utf-8")).hexdigest()

def load_seen():
    if STATE.exists():
        try:
            return set(json.loads(STATE.read_text()))
        except Exception:
            return set()
    return set()

def save_seen(ids):
    STATE.write_text(json.dumps(sorted(ids)))

def build_rss(items):
    now = dt.datetime.now(dt.timezone.utc)
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "itch.io Charity Bundles — Opportunities"
    SubElement(channel, "link").text = "https://itch.io"
    SubElement(channel, "description").text = "Auto-collected posts and jams related to charity/fundraisers on itch.io (fresh-only)."
    SubElement(channel, "lastBuildDate").text = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

    for it in items:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = it["title"]
        SubElement(item, "link").text = it["link"]
        SubElement(item, "guid").text = hash_item(it)
        when = it.get("ts") or now
        SubElement(item, "pubDate").text = when.strftime("%a, %d %b %Y %H:%M:%S +0000")
        SubElement(item, "description").text = it.get("summary") or it["title"]

    ElementTree(rss).write(OUT_FEED, encoding="utf-8", xml_declaration=True)

def main():
    seen = load_seen()
    new_seen = set(seen)
    found = []

    for url, label in SOURCES:
        try:
            html = get(url)
            for it in items_from_html(url, html, label):
                it["id"] = hash_item(it)
                if it["id"] not in seen:
                    found.append(it)
                new_seen.add(it["id"])
            time.sleep(1)  # polite between sources
        except Exception as e:
            print("WARN:", url, e)

    # Keep the latest ~50
    combined = found[-50:]
    build_rss(combined)
    save_seen(new_seen)
    print(f"Wrote {len(combined)} items to {OUT_FEED}")

if __name__ == "__main__":
    main()
