# Data Update Guide

How to keep FEC and ISBE data current in the Illinois Campaign Finance Tracker.

---

## FEC (Federal) Updates

FEC data is available via the OpenFEC API and can be fully automated.

### Automated Weekly Sync

The `scripts/sync-fec.sh` wrapper runs three steps in sequence:

1. **sync-fec-il-federal** — Pull latest Schedule A contributions for Illinois federal candidates
2. **rebuild-fec-donor-identities** — Re-cluster donor entities for drill-down views
3. **refresh-analytics --with-snapshot** — Rebuild analytics tables and save a point-in-time snapshot

#### systemd Timer Setup (recommended)

Create two unit files on the VPS:

**`/etc/systemd/system/il-campaign-fec-sync.service`**

```ini
[Unit]
Description=Illinois Campaign Finance - FEC weekly sync
After=network-online.target

[Service]
Type=oneshot
User=app
WorkingDirectory=/srv/illinois_campaign_finance/current
ExecStart=/srv/illinois_campaign_finance/current/scripts/sync-fec.sh
EnvironmentFile=/srv/illinois_campaign_finance/shared/.env
```

**`/etc/systemd/system/il-campaign-fec-sync.timer`**

```ini
[Unit]
Description=Run FEC sync every Sunday at 3 AM UTC

[Timer]
OnCalendar=Sun *-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now il-campaign-fec-sync.timer
```

Check status:

```bash
systemctl list-timers il-campaign-fec-sync.timer
journalctl -u il-campaign-fec-sync.service --since today
```

#### Prerequisites

- `FEC_API_KEY` must be set in `/srv/illinois_campaign_finance/shared/.env`
- Request a key at https://api.open.fec.gov/developers/

#### Manual FEC Sync

Run the same steps by hand from the project root:

```bash
python run.py sync-fec-il-federal --cycle 2026 --contributor-state IL --max-calls 900
python run.py rebuild-fec-donor-identities
python run.py refresh-analytics --with-snapshot
```

Or use the wrapper script directly:

```bash
bash scripts/sync-fec.sh
```

---

## ISBE (State) Updates

The Illinois State Board of Elections does **not** provide a public API. Data must be obtained through their website. Below are four approaches, ranked by recommendation.

### Option 1: Manual Bulk Download (Recommended)

The most reliable method. Download bulk data files from the ISBE website, transfer to the server, and import.

1. Visit the ISBE Campaign Disclosure page and download the bulk CSV/TXT files
2. Transfer files to the server:
   ```bash
   scp *.txt user@server:/srv/illinois_campaign_finance/shared/downloads/
   ```
3. Run the bulk import:
   ```bash
   python run.py import-bulk-download --input-dir /srv/illinois_campaign_finance/shared/downloads/
   python run.py refresh-analytics --with-snapshot
   ```

**Pros:** Most reliable, complete dataset, no scraping fragility.
**Cons:** Requires manual download every time.

### Option 2: Automated Web Scraping

Use the existing Playwright-based scrapers to pull incremental updates automatically via cron.

```bash
python run.py scrape-main --resume
python run.py refresh-analytics --with-snapshot
```

This can be scheduled similarly to the FEC timer, but be aware that ISBE website changes may break scrapers without notice.

**Pros:** Fully automated, incremental updates.
**Cons:** Fragile — website layout changes break scrapers; may miss bulk-only data.

### Option 3: Playwright Headless Bulk Download (Future)

Automate the bulk file download itself using Playwright to navigate the ISBE site, trigger the download, and pipe the files into the import pipeline. This is not yet implemented but could combine the reliability of Option 1 with the automation of Option 2.

**Pros:** Automated + complete dataset.
**Cons:** Not yet built; ISBE site changes could still break it.

### Option 4: Calendar Reminder + Manual Re-download

Set a recurring calendar reminder (e.g. monthly) to manually download and import bulk files per Option 1. Lowest effort to set up, relies on discipline to follow through.

**Pros:** Zero infrastructure to maintain.
**Cons:** Entirely manual; easy to forget.

---

## Post-Update Verification

After any data update (FEC or ISBE), verify the results:

1. Check the dashboard at `/` for updated stat cards and freshness dates
2. Visit `/candidates` to confirm both state and federal sections show current numbers
3. Review the analytics dashboard at `/analytics` for refreshed metrics
