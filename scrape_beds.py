#!/usr/bin/env python3
"""
Daily snapshot of ribhopenbeds.org (Rhode Island BH open beds + recovery housing).

Runs on GitHub Actions once a day (plus a backup run). Uses only built-in Python.

Each run:
  1. Stops quietly if the 90-day collection is already finished.
  2. Skips if today's (Eastern time) snapshot is already saved, so the backup
     run can never create duplicates.
  3. Downloads both data feeds (3 tries each).
  4. Refuses to save if the site returns far fewer rows than usual
     (a sign the site changed), so junk never enters the dataset.
  5. Saves the untouched replies to data/raw/ and appends clean rows
     to data/beds_daily.csv.
  6. Writes a short status note (Day N of 90, any missed days).

Exit codes: 0 = OK or skipped, 1 = failed, 3 = saved today but yesterday was missed.
"""
import csv
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TARGET_DAYS = int(os.environ.get("TARGET_DAYS", "90"))
ET = ZoneInfo("America/New_York")
URL = "https://ribhopenbeds.org/wp-admin/admin-ajax.php"
FEEDS = {"beds": "getbedlist", "recovery_housing": "gethousinglist"}
MIN_ROWS = 10  # absolute floor per feed

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CSV_PATH = DATA / "beds_daily.csv"
STATUS_PATH = DATA / "STATUS.md"

COLUMNS = [
    "snapshot_date", "snapshot_time_et", "feed", "provider", "provider_slug",
    "bed_type", "service", "setting", "town", "status",
    "available", "filled", "waiting", "total_reported_beds",
    "provider_updated_et", "hours_since_provider_update",
]


def summary(msg):
    """Print to the log, and to the GitHub run page if we're on GitHub."""
    print(msg)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n\n")


def fetch(action):
    body = urllib.parse.urlencode({"action": action}).encode()
    req = urllib.request.Request(
        URL, data=body,
        headers={"User-Agent": "ribeds-daily-snapshot/1.0 (research; once per day)"},
    )
    last_err = None
    for attempt, wait in enumerate([0, 20, 60], start=1):
        if wait:
            print(f"  retrying {action} in {wait}s ...")
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                text = r.read().decode("utf-8")
            payload = json.loads(text)
            rows = payload["data"] if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise ValueError("feed did not contain a list of rows")
            return text, rows
        except Exception as e:  # network hiccup, bad JSON, site error page
            last_err = e
            print(f"  attempt {attempt} for {action} failed: {e}")
    raise RuntimeError(f"Could not download '{action}' after 3 tries: {last_err}")


def clean(s):
    if s is None:
        return ""
    s = html.unescape(re.sub(r"<[^>]+>", "", str(s)))
    s = s.replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def to_int(v):
    s = clean(v)
    return int(s) if re.fullmatch(r"-?\d+", s) else ""


def parse_rows(feed, rows, now_et):
    out = []
    for r in rows:
        prov_html = str(r.get("provider", ""))
        m = re.search(r"/provider/([^/\"']+)/?", prov_html)
        upd = r.get("updated") or {}
        ts = upd.get("sort") if isinstance(upd, dict) else None
        if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
            upd_dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(ET)
            upd_str = upd_dt.strftime("%Y-%m-%d %H:%M")
            age = round((now_et - upd_dt).total_seconds() / 3600, 1)
        else:
            upd_str = clean(upd.get("display") if isinstance(upd, dict) else upd)
            age = ""
        avail, filled = to_int(r.get("available")), to_int(r.get("filled"))
        out.append({
            "snapshot_date": now_et.date().isoformat(),
            "snapshot_time_et": now_et.strftime("%H:%M"),
            "feed": feed,
            "provider": clean(prov_html),
            "provider_slug": m.group(1) if m else "",
            "bed_type": clean(r.get("bed-type")),
            "service": clean(r.get("service")),
            "setting": clean(r.get("setting")),
            "town": clean(r.get("town")),
            "status": clean(r.get("status")),
            "available": avail,
            "filled": filled,
            "waiting": to_int(r.get("waiting")),
            "total_reported_beds": (avail + filled) if avail != "" and filled != "" else "",
            "provider_updated_et": upd_str,
            "hours_since_provider_update": age,
        })
    return out


def load_history():
    """Return {date: {feed: row_count}} from the CSV."""
    hist = {}
    if CSV_PATH.exists():
        with open(CSV_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = hist.setdefault(row["snapshot_date"], {})
                d[row["feed"]] = d.get(row["feed"], 0) + 1
    return hist


def missing_days(dates, today):
    if not dates:
        return []
    have = {date.fromisoformat(d) for d in dates}
    first = min(have)
    return [
        (first + timedelta(n)).isoformat()
        for n in range((today - first).days + 1)
        if first + timedelta(n) not in have
    ]


def write_status(hist, today, note):
    dates = sorted(hist)
    gaps = missing_days(dates, today)
    lines = [
        "# Collection status",
        "",
        f"- Last checked: {datetime.now(ET):%Y-%m-%d %H:%M} ET",
        f"- {note}",
        f"- Days collected: **{len(dates)} of {TARGET_DAYS}**",
        f"- First day: {dates[0] if dates else '-'} | Latest day: {dates[-1] if dates else '-'}",
        f"- Missed days: {', '.join(gaps) if gaps else 'none'}",
    ]
    STATUS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    DATA.mkdir(exist_ok=True)
    RAW.mkdir(exist_ok=True)
    now_et = datetime.now(ET).replace(microsecond=0)
    today = now_et.date().isoformat()
    hist = load_history()

    if len(hist) >= TARGET_DAYS and today not in hist:
        summary(f"FINISHED: all {TARGET_DAYS} days are collected. Nothing to do. "
                "You can turn off the schedule (see README, 'Stopping').")
        return 0
    if today in hist:
        summary(f"SKIPPED: {today} is already saved (Day {len(hist)} of {TARGET_DAYS}).")
        return 0

    print(f"Taking snapshot for {today} at {now_et:%H:%M} ET")
    raw_texts, new_rows = {}, []
    for feed, action in FEEDS.items():
        text, rows = fetch(action)
        # Sanity check against the floor and against the last saved day
        prev = hist[max(hist)].get(feed, 0) if hist else 0
        floor = max(MIN_ROWS, prev // 2)
        if len(rows) < floor:
            raise RuntimeError(
                f"'{feed}' feed returned only {len(rows)} rows (expected at least {floor}; "
                f"last saved day had {prev}). The site may have changed - nothing was saved."
            )
        parsed = parse_rows(feed, rows, now_et)
        if not any(r["provider"] and r["bed_type"] for r in parsed):
            raise RuntimeError(f"'{feed}' rows had no provider/bed type - site format may have changed.")
        raw_texts[feed] = text
        new_rows += parsed
        print(f"  {feed}: {len(rows)} rows")

    # Save raw backups, then append clean rows
    for feed, text in raw_texts.items():
        (RAW / f"{today}_{feed}.json").write_text(text, encoding="utf-8")
    new_file = not CSV_PATH.exists()
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        if new_file:
            w.writeheader()
        w.writerows(new_rows)

    hist = load_history()
    day_n = len(hist)
    counts = ", ".join(f"{k} {v}" for k, v in hist[today].items())
    note = f"Day {day_n} of {TARGET_DAYS} saved ({counts} rows)."
    gaps = missing_days(sorted(hist), now_et.date())
    yesterday = (now_et.date() - timedelta(1)).isoformat()
    write_status(hist, now_et.date(), note)
    summary(f"SAVED: {note}")
    if gaps:
        summary(f"Missed days so far: {', '.join(gaps)}")
    if day_n >= TARGET_DAYS:
        summary(f"That was the last day. All {TARGET_DAYS} days are collected.")
    if yesterday in gaps:
        summary(f"WARNING: no data was saved for {yesterday}. That day can't be recovered, "
                "but today is saved. This run is marked failed only so GitHub emails you.")
        return 3
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        summary(f"FAILED: {e}")
        sys.exit(1)
