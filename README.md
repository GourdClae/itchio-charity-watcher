
# itch.io Charity Bundle Watcher (RSS)

This repo builds an RSS feed (`feed.xml`) of posts that look like **charity bundles accepting submissions** on itch.io, so you can subscribe in any feed reader.

## What is RSS?
RSS is a simple, standardized **news feed** format. Apps like **Feedly**, **Inoreader**, **NetNewsWire** (Mac/iOS), or **Feeder** (Android) can subscribe to a URL and show new items automatically.

## How this works
- A small Python script scrapes a few itch.io pages (blog/community/bundles) and keeps only items that match both:
  - A **charity** keyword (e.g., "charity", "fundraiser", "donation", "mutual aid").
  - A **submission** keyword (e.g., "accepting submissions", "call for submissions").
- The script writes `feed.xml` and remembers what it has seen in `.seen.json`.
- GitHub Actions runs the script on a schedule and commits the updated `feed.xml` back to the repo.
- GitHub Pages hosts `feed.xml` at a public URL that your RSS reader can subscribe to.

## One-time setup (about 10 minutes)
1. **Create a new repo** on GitHub (public or private).
2. **Upload these files** (or push with Git):
   - `itchio_charity_bundles_feed.py`
   - `requirements.txt`
   - `.github/workflows/build.yml`
   - `.gitignore`
3. **Run it once locally (optional, good test):**
   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   python itchio_charity_bundles_feed.py
   ```
   You should see `feed.xml` appear.
4. **Push to GitHub.**

## Turn on GitHub Pages
- Go to **Settings → Pages**.
- Under **Build and deployment**, set:
  - **Source:** "Deploy from a branch"
  - **Branch:** `main`, **Folder:** `/ (root)`
- Save. After a minute, Pages will give you a URL like:
  `https://<your-username>.github.io/<repo>/feed.xml`

## Subscribe in your RSS reader
Copy the `feed.xml` URL and add it to your RSS app as a new feed.

## Customize filters (optional)
Open `itchio_charity_bundles_feed.py` and edit:
- `CHARITY` and `SUBMIT` regexes to adjust what counts.
- `SOURCES` to add/remove pages (e.g., specific community sub-forums).

## Change the schedule
Edit `.github/workflows/build.yml`:
- The default is **daily at 13:00 UTC** (09:00 US/Eastern).
- Use [crontab.guru](https://crontab.guru/) to pick a different cron, then update the `cron:` line.

## Troubleshooting
- If `feed.xml` isn’t updating:
  - Check the **Actions** tab on GitHub for logs.
  - If Pages isn’t serving the file, confirm Pages is set to **root** of `main`.
- If you see false positives/negatives, tweak the regexes and/or sources.

Enjoy!
