# itchio_charity_bundles_feed.py
# Builds an RSS feed (feed.xml) of likely charity bundle/jam opportunities on itch.io.
# Sources:
#   - Blog index (filters by charity keywords)                      -> [BLOG]
#   - Game Jams board (follows thread pages 1 click deep)           -> [BOARD]
#   - Jams "Starting This Month" (+ Starting Soon; follows pages)   -> [JAMS]
#
# NOTE: SUBMISSION keyword requirement removed — now only charity keywords are required.

import re, json, time, hashlib, datetime as dt
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup as BS
from xml.etree.ElementTree import Element, SubElement, ElementTree

USER_AGENT = "itchio-charity-watcher/1.5"
OUT_FEED = Path("feed.xml")
STATE = Path(".seen.json")

# --- Keyword filters (charity only) ------------------------------------------
CHARITY = re.compile(
    r"\b(charity|fundraiser|donation|relief|mutual aid|non[- ]?profit|benefit|fund(?:\s*raiser)?)\b",
    re.I,
)

# --- Sources -----------------------------------------------------------------
SOURCES = [
    ("https://itch.io/blog", "[BLOG]"),
    ("https://itch.io/board/533649/game-jams", "[BOARD]"),              # Game Jams board (current URL)
    ("https://itch.io/jams/starting-this-month", "[JAMS]"),             # Jams starting this month
    ("https://itch.io/jams/starting-this-month/sort-date", "[JAMS]"),   # "Starting Soon" sort
]

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

# --- Jam listing parsing (exclude 'Ended …' AND follow jam pages) ------------
JAM_STATUS_HINT = re.compile(r"(Starts in|Submission closes in|Ended)", re.I)

def parse_iso(ts: str):
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return dt.datetime.fromisoformat(ts)
    except Exception:
        return None

def extract_text(elem) -> str:
    return " ".join((elem.get_text(" ") if elem else "").split())

def jam_page_matches(full_html: str) -> tuple[bool, str]:
    """Check full jam page for charity keywords; return (match, summary_text)."""
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
    match = bool(CHARITY.search(text))  # <-- only charity terms required
    return match, text[:280]

def items_from_jams_listing(url: str, html: str, label: str, max_jams: int = 30):
    """From a 'starting this month' listing, collect jam cards, drop 'Ended',
    then follow each jam page and apply full-body charity keyword checks."""
    soup = BS(html, "html.parser")
    out = []
    now = dt.datetime.now(dt.timezone.utc)

    # Collect unique jam links
    seen_links = []
    for a in soup.select("a[href*='/jam/']"):
        link = to_abs(a.get("href") or "")
        if link.startswith("https://itch.io/jam/") and link not in seen_links:
            seen_links.append(link)

    # Filter cards first (skip clearly ended; prefer future-dated)
    kept_links = []
    for link in seen_links:
        anchor = soup.find("a", href=lambda h: h and (link.endswith(h) or h == link.replace("https://itch.io", "")))
        container = anchor
        for _ in range(3):
            if container and container.parent:
                container = container.parent
        text_blob = extract_text(container) if container else (extract_text(anchor) if anchor else "")
        ended = bool(re.search(r"\bEnded\b", text_blob, re.I))
        if ended:
            continue

        ts_val = None
        t = container.find("time") if container else None
        if t and t.has_attr("datetime"):
            ts_val = parse_iso(t["datetime"])
            if ts_val and ts_val.tzinfo is None:
                ts_val = ts_val.replace(tzinfo=dt.timezone.utc)

        starts_in = bool(re.search(r"\bStarts in\b", text_blob, re.I))
        closes_in = bool(re.search(r"\bSubmission closes in\b", text_blob, re.I))

        if ts_val:
            if (starts_in or closes_in) and ts_val > now:
                kept_links.append(link)
        else:
            if starts_in or closes_in:
                kept_links.append(link)

        if len(kept_links) >= max_jams:
            break

    # Follow jam pages and run charity-only checks
    for jlink in kept_links:
        try:
            jhtml = get(jlink)
            ok, snippet = jam_page_matches(jhtml)
            if ok:
                jsoup = BS(jhtml, "html.parser")
                title = extract_text(jsoup.select_one("h1, .jam_title, .header_title")) or "Jam"
                out.append({
                    "title": f"{label} {title}"[:160],
                    "link": jlink,
                    "summary": snippet
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

    # Jam listings (starting this month)
    if url.startswith("https://itch.io/jams/starting-this-month"):
        return items_from_jams_listing(url, html, label)

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
            if CHARITY.search(blob):  # <-- charity-only
                candidates.append({
                    "title": f"{label} {text}"[:160],
                    "link": href,
                    "summary": snippet[:280]
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
                time.sleep(1)
            except Exception as e:
                print("WARN thread:", tlink, e)
        return candidates

    # Generic page scan (thread pages land here)
    for a in soup.select("a"):
        href = to_abs(a.get("href") or "")
        text = extract_text(a)
        if not href or not text or not href.startswith("https://itch.io"):
            continue
        parent = a.find_parent()
        snippet = extract_text(parent)[:500] if parent else ""
        blob = f"{text} — {snippet}"
        if CHARITY.search(blob):  # <-- charity-only
            candidates.append({
                "title": f"{label} {text}"[:160],
                "link": href,
                "summary": snippet[:280]
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
    SubElement(channel, "description").text = "Auto-collected posts and jams related to charity/fundraisers on itch.io."
    SubElement(channel, "lastBuildDate").text = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

    for it in items:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = it["title"]
        SubElement(item, "link").text = it["link"]
        SubElement(item, "guid").text = hash_item(it)
        SubElement(item, "pubDate").text = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
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

    combined = found[-50:]  # keep last ~50
    build_rss(combined)
    save_seen(new_seen)
    print(f"Wrote {len(combined)} items to {OUT_FEED}")

if __name__ == "__main__":
    main()
