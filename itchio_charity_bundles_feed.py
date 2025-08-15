# itchio_charity_bundles_feed.py
# Creates feed.xml with items that look like charity bundle calls for submissions on itch.io.
# Run locally:  python itchio_charity_bundles_feed.py
# Outputs: feed.xml (RSS) and .seen.json (state)

import re, json, time, hashlib, datetime as dt
from pathlib import Path
import requests
from bs4 import BeautifulSoup as BS
from xml.etree.ElementTree import Element, SubElement, ElementTree

USER_AGENT = "itchio-charity-watcher/1.1"
OUT_FEED = Path("feed.xml")
STATE = Path(".seen.json")

# Keywords: require one from CHARITY and one from SUBMIT in title or nearby text
CHARITY = re.compile(r"\b(charity|fundraiser|donation|relief|mutual aid|non[- ]?profit|benefit)\b", re.I)
SUBMIT  = re.compile(r"\b(accepting submissions|submissions open|submit your game|call for submissions|contributors? wanted|seeking entries|open call)\b", re.I)

# Real pages to scan (no /blog/search — it doesn't exist)
SOURCES = [
    "https://itch.io/blog",      # blog index; we'll target post links under /blog/
    "https://itch.io/community", # community landing with many bundle/call threads
    "https://itch.io/bundles",   # bundles directory; filtered by our keywords
]

def get(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text

def normalize_abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("/"):
        return "https://itch.io" + href
    return href

def items_from_page(url: str, html: str):
    soup = BS(html, "html.parser")

    # Prefer specific anchors on the blog index to avoid nav/footer noise
    if url.rstrip("/") == "https://itch.io/blog":
        anchors = soup.select("a[href*='/blog/']") or soup.select("a")
    else:
        anchors = soup.select("a")

    candidates = []
    for a in anchors:
        href = normalize_abs(a.get("href") or "")
        text = " ".join(a.get_text(" ").split())
        if not href or not text:
            continue
        if not href.startswith("https://itch.io"):
            continue

        # Pull nearby text as a snippet for keyword matching
        snippet = ""
        parent = a.find_parent()
        if parent:
            snippet = " ".join(parent.get_text(" ").split())[:500]

        blob = f"{text} — {snippet}"
        if CHARITY.search(blob) and SUBMIT.search(blob):
            candidates.append({
                "title": text[:140],
                "link": href,
                "summary": snippet[:280]
            })
    return candidates

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
    # Use timezone-aware UTC to avoid deprecation warnings
    now = dt.datetime.now(dt.timezone.utc)
    rss = Element("rss", version="2.0")
    channel = SubElement(rss, "channel")
    SubElement(channel, "title").text = "itch.io Charity Bundles — Calls for Submissions"
    SubElement(channel, "link").text = "https://itch.io"
    SubElement(channel, "description").text = "Auto-collected posts that look like charity bundles accepting submissions on itch.io."
    SubElement(channel, "lastBuildDate").text = now.strftime("%a, %d %b %Y %H:%M:%S +0000")

    for it in items:
        item = SubElement(channel, "item")
        SubElement(item, "title").text = it["title"]
        SubElement(item, "link").text = it["link"]
        SubElement(item, "guid").text = it["id"]
        SubElement(item, "pubDate").text = now.strftime("%a, %d %b %Y %H:%M:%S +0000")
        SubElement(item, "description").text = it["summary"] or it["title"]

    ElementTree(rss).write(OUT_FEED, encoding="utf-8", xml_declaration=True)

def main():
    seen = load_seen()
    new_seen = set(seen)
    found = []

    for url in SOURCES:
        try:
            html = get(url)
            for it in items_from_page(url, html):
                it["id"] = hash_item(it)
                if it["id"] not in seen:
                    found.append(it)
                new_seen.add(it["id"])
            time.sleep(1)  # be polite
        except Exception as e:
            print("WARN:", url, e)

    # Keep the latest ~50 items (new first)
    combined = found[-50:]
    build_rss(combined)
    save_seen(new_seen)
    print(f"Wrote {len(combined)} items to {OUT_FEED}")

if __name__ == "__main__":
    main()
