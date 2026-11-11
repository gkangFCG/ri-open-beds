#!/usr/bin/env python3
"""
Daily snapshot of Rhode Island behavioral health bed availability.

Version 2 (Nov 2026): ribhopenbeds.org was replaced by BHDDH's Salesforce
"Provider Portal" (https://ribhddh.my.site.com/ProviderPortal/s/availability).
This version reads the same public data that page shows, by calling the page's
own data service directly. It uses only built-in Python (no browser needed).

Each run:
  1. Stops quietly if 90 days have already been collected.
  2. Skips if today's (Eastern time) snapshot is already saved, so the backup
     run can never create duplicates.
  3. Downloads inpatient/residential beds and recovery housing (3 tries).
  4. Refuses to save if the site returns far fewer rows than usual
     (a sign the site changed), so junk never enters the dataset.
  5. Saves the untouched replies to data/raw/ and appends clean rows
     to data/beds_daily.csv.
  6. Writes a short status note (Day N of 90, any missed days).

Exit codes: 0 = OK or skipped, 1 = failed, 3 = saved today but yesterday was missed.
"""
import csv
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
BASE = "https://ribhddh.my.site.com/ProviderPortal"
PAGE = "/ProviderPortal/s/availability"
UA = {"User-Agent": "ribeds-daily-snapshot/2.0 (research; once per day)"}
SOURCE = "bhddh_portal"
FEEDS = {
    "beds": ("fetchBedInventoryRecords",
             {"filters": {"status": "ALL", "agency": "ALL", "bedType": "ALL",
                          "service": "ALL", "setting": "ALL", "useAccountName": True}}),
    "recovery_housing": ("fetchRecoveryHousingBedInventory",
                         {"filters": {"status": "ALL", "agency": "ALL", "bedType": "ALL",
                                      "narrLevelOfSupport": "ALL"}}),
}
MIN_ROWS = 10  # absolute floor per feed

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
RAW = DATA / "raw"
CSV_PATH = DATA / "beds_daily.csv"
STATUS_PATH = DATA / "STATUS.md"

# The first 16 columns are the original layout; the rest were added for the new portal.
COLUMNS = [
    "snapshot_date", "snapshot_time_et", "feed", "provider", "provider_slug",
    "bed_type", "service", "setting", "town", "status",
    "available", "filled", "waiting", "total_reported_beds",
    "provider_updated_et", "hours_since_provider_update",
    "source", "record_id", "agency_id", "narr_level", "blocked", "previous_day_discharge",
]


def summary(msg):
    """Print to the log, and to the GitHub run page if we're on GitHub."""
    print(msg)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(msg + "\n\n")


def http(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8")


def page_context():
    """Read the framework IDs the data service expects from the public page."""
    html_text = http(BASE + "/s/availability")
    for m in re.findall(r'/ProviderPortal/s/sfsites/l/([^/"]+)/', html_text):
        try:
            j = json.loads(urllib.parse.unquote(m))
        except ValueError:
            continue
        if "fwuid" in j and "loaded" in j:
            return {"mode": "PROD", "fwuid": j["fwuid"], "app": j.get("app", "siteforce:communityApp"),
                    "loaded": j["loaded"], "dn": [], "globals": {}, "uad": True}
    raise RuntimeError("Could not find the portal's page settings - the availability page may have changed.")


def call_apex(ctx, method, params):
    msg = {"actions": [{"id": "1;a", "descriptor": "aura://ApexActionController/ACTION$execute",
                        "callingDescriptor": "UNKNOWN",
                        "params": {"namespace": "", "classname": "RI_AvailabilityController",
                                   "method": method, "params": params,
                                   "cacheable": False, "isContinuation": False}}]}
    body = urllib.parse.urlencode({"message": json.dumps(msg), "aura.context": json.dumps(ctx),
                                   "aura.pageURI": PAGE, "aura.token": "null"}).encode()
    text = http(BASE + "/s/sfsites/aura?r=1&aura.ApexAction.execute=1", data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
    text = re.sub(r"^\s*(while\(1\);|for\s*\(;;\);)", "", text)  # Salesforce anti-hijacking prefix
    payload = json.loads(text)
    action = payload["actions"][0]
    if action.get("state") != "SUCCESS":
        raise RuntimeError(f"{method} returned {action.get('state')}: {str(action.get('error'))[:300]}")
    rows = action["returnValue"]["returnValue"]
    if not isinstance(rows, list):
        raise ValueError(f"{method} did not return a list of rows")
    return text, rows


def fetch_all():
    last_err = None
    for attempt, wait in enumerate([0, 20, 60], start=1):
        if wait:
            print(f"  retrying in {wait}s ...")
            time.sleep(wait)
        try:
            ctx = page_context()  # re-read each try: Salesforce updates these IDs a few times a year
            return {feed: call_apex(ctx, method, params) for feed, (method, params) in FEEDS.items()}
        except Exception as e:  # network hiccup, framework update, site error
            last_err = e
            print(f"  attempt {attempt} failed: {e}")
    raise RuntimeError(f"Could not download the portal data after 3 tries: {last_err}")


def clean(s):
    if s is None:
        return ""
    s = str(s).replace("–", "-").replace("—", "-").replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def to_int(v):
    s = clean(v)
    return int(float(s)) if re.fullmatch(r"-?\d+(\.0+)?", s) else ""


def parse_rows(feed, rows, now_et):
    out = []
    for r in rows:
        ts = r.get("lastUpdated")
        try:
            upd_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(ET)
            upd_str = upd_dt.strftime("%Y-%m-%d %H:%M")
            age = round((now_et - upd_dt).total_seconds() / 3600, 1)
        except (TypeError, ValueError):
            upd_str, age = clean(ts), ""
        avail, filled = to_int(r.get("available")), to_int(r.get("filled"))
        out.append({
            "snapshot_date": now_et.date().isoformat(),
            "snapshot_time_et": now_et.strftime("%H:%M"),
            "feed": feed,
            "provider": clean(r.get("agency")),
            "provider_slug": "",  # old-site ID; not used by the new portal (see agency_id)
            "bed_type": clean(r.get("bedType")),
            "service": clean(r.get("service")),
            "setting": clean(r.get("setting")),
            "town": clean(r.get("cityTown")),
            "status": clean(r.get("status")),
            "available": avail,
            "filled": filled,
            "waiting": to_int(r.get("waiting")),
            "total_reported_beds": (avail + filled) if avail != "" and filled != "" else "",
            "provider_updated_et": upd_str,
            "hours_since_provider_update": age,
            "source": SOURCE,
            "record_id": clean(r.get("id")),
            "agency_id": clean(r.get("agencyId")),
            "narr_level": clean(r.get("narrLevelOfSupport")),
            "blocked": to_int(r.get("blocked")),
            "previous_day_discharge": to_int(r.get("previousDayDischarge")),
        })
    return out


def migrate_csv():
    """Add the new columns to an existing CSV (old rows are marked source=old_site)."""
    if not CSV_PATH.exists():
        return
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames == COLUMNS:
            return
        rows = list(reader)
    for r in rows:
        r.setdefault("source", "")
        if not r["source"]:
            r["source"] = "old_site"
    tmp = CSV_PATH.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(CSV_PATH)
    print(f"Upgraded {CSV_PATH.name} to the new column layout ({len(rows)} existing rows kept).")


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
        f"- Data source: BHDDH Provider Portal ({BASE}/s/availability). "
        "Days before the switch came from the old ribhopenbeds.org feed (source = old_site).",
    ]
    STATUS_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    DATA.mkdir(exist_ok=True)
    RAW.mkdir(exist_ok=True)
    migrate_csv()
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
    results = fetch_all()
    new_rows = []
    for feed, (text, rows) in results.items():
        prev = hist[max(hist)].get(feed, 0) if hist else 0
        floor = max(MIN_ROWS, prev // 2)
        if len(rows) < floor:
            raise RuntimeError(
                f"'{feed}' returned only {len(rows)} rows (expected at least {floor}; "
                f"last saved day had {prev}). The site may have changed - nothing was saved."
            )
        parsed = parse_rows(feed, rows, now_et)
        if not any(r["provider"] and r["bed_type"] for r in parsed):
            raise RuntimeError(f"'{feed}' rows had no provider/bed type - site format may have changed.")
        new_rows += parsed
        print(f"  {feed}: {len(rows)} rows")

    for feed, (text, _) in results.items():
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
