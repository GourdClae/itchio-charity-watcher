# itchio_charity_bundles_feed.py
# Creates feed.xml with items that look like charity bundle calls for submissions on itch.io.
# Run locally:  python itchio_charity_bundles_feed.py
# Outputs: feed.xml (RSS) and .seen.json (state)
# Schedule with GitHub Actions to auto-update daily.

import re, json, time, hashlib, datetime as dt
from pathlib import Path
import requests
from bs4 import BeautifulSoup as BS
from xml.etree.ElementTree import Element, SubElement, ElementTree

USER_AGENT = "itchio-charity-watcher/1.0"
OUT_FEED = Path("feed.xml")
STATE = Path(".seen.json")

# You can tweak these keyword lists as you learn which phrasing organizers use.
CHARITY = re.compile(r"\\b(charity|fundraiser|donation|relief|mutual aid|non[- ]?profit|benefit)\\b", re.I)
SUBMIT  = re.compile(r"\\b(accepting submissions|submissions open|submit your game|call for submissions|contributors? wanted|seeking entries|open call)\\b", re.I)

# Add or remove sources here. The script is conservative and only keeps items that match both CHARITY and SUBMIT.
SOURCES = [
    "https://itch.io/blog/search?q=bundle",
    "https://itch.io/blog/search?q=charity",
    "https://itch.io/blog/search?q=submissions",
    "https://itch.io/community",
    "https://itch.io/blog",
    # Optional: Bundles directory (filtered hard by keywords):
    "https://itch.io/bundles",
]

def get(url):
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text

def items_from_page(url, html):
    soup = BS(html, "html.parser")
    candidates = []

    # Collect anchors; use nearby text as a snippet
    for a in soup.select("a"):
        href = a.get("href") or ""
        text = " ".join(a.get_text(" ").split())
        if not href or not text:
            continue

        # Absolute URL
        if href.startswith("/"):
            href = "https://itch.io" + href
        if not href.startswith("https://itch.io"):
            continue

        # Neighborhood snippet
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

def hash_item(it):
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
    now = dt.datetime.utcnow()
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
            time.sleep(1)
        except Exception as e:
            print("WARN:", url, e)

    # Keep last ~50
    combined = found[-50:]
    build_rss(combined)
    save_seen(new_seen)
    print(f"Wrote {len(combined)} items to {OUT_FEED}")

if __name__ == "__main__":
    main()
