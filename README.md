# RI Open Beds: 90-day daily snapshot

Once a day, GitHub's computers download the bed counts from
[ribhopenbeds.org](https://ribhopenbeds.org) and save them here. Nobody's laptop
needs to be on.

- **The data:** `data/beds_daily.csv` (one row per provider + bed type per day)
- **Progress at a glance:** `data/STATUS.md` (shows "Day N of 90" and any missed days)
- **Backups of the original downloads:** `data/raw/`

---

## One-time setup (about 15 minutes, all in a web browser)

You need the unzipped `ri-open-beds` folder. It has three files:
`scrape_beds.py`, `README.md` and `daily-snapshot.yml`.

### Step 1: Make a free GitHub account
1. Go to **github.com/signup**.
2. Sign up with your work email and pick the **Free** plan.
3. GitHub emails you a code. Type it in to confirm your email.

### Step 2: Make a private "repository" (a project folder on GitHub)
1. Click the **+** in the top-right corner, then click **New repository**.
2. Under **Repository name**, type: `ri-open-beds`
3. Choose **Private**.
4. Turn on **Add a README file**. This just stops the folder from starting empty.
5. Click **Create repository**.

### Step 3: Upload the two main files
1. On your new repository page, click **Add file**, then **Upload files**.
2. Drag in **`scrape_beds.py`** and **`README.md`**. Upload *only* those two for now.
   (Your README replaces the blank one GitHub made.)
3. Scroll down and click the green **Commit changes** button.

### Step 4: Add the schedule file
This file has to go in a special folder, so you create it on GitHub instead of uploading it.
1. Click **Add file**, then **Create new file**.
2. In the file-name box at the top, type this exactly. The folders appear on their own as you type each `/`:
   `.github/workflows/daily-snapshot.yml`
3. On your Mac, right-click **`daily-snapshot.yml`**, choose **Open With**, then **TextEdit**.
4. In TextEdit, press **⌘A** (select all), then **⌘C** (copy).
5. Back in GitHub, click in the big empty box and press **⌘V** (paste).
6. Click **Commit changes**, then click **Commit changes** again in the pop-up.

### Step 5: Test it now (this also saves Day 1)
1. Click the **Actions** tab at the top of the repository.
2. On the left, click **Daily bed snapshot**.
3. On the right, click **Run workflow**, then the green **Run workflow** button.
4. Wait about 30 seconds and refresh the page. A yellow dot means it's running.
   A **green check ✅** means it worked.
5. Click the **Code** tab. You should now see a `data` folder. Open `data/STATUS.md`
   and it should say **Day 1 of 90**.

If you see a **red X ❌** instead, see *Troubleshooting* below.

### Step 6: Make sure failure emails are on
1. Click your profile picture (top-right corner), then **Settings**, then **Notifications** (left menu).
2. Find **Actions** (under *System*). Make sure **Email** is checked and
   **Only notify for failed workflows** is turned on.

**Important:** GitHub sends failure emails to *the person who created the schedule file in Step 4*,
so that should be whoever is keeping an eye on this.

---

## After setup: what happens every day

- **Two tries a day:** about **6 pm** and **9:40 pm** Eastern. (Until Nov 1 these are
  7:13 pm and 10:41 pm, because of daylight saving time.) If the first try works,
  the second just says "SKIPPED". GitHub sometimes starts runs up to an hour late,
  and that's normal.
- **Quiet means good.** You only get an email if something goes wrong.
- **Weekly 30-second check:** open `data/STATUS.md`. Look at "Days collected" and
  "Missed days".
- **It stops on its own.** After 90 days are saved, each run says "FINISHED" and does nothing.

### What the emails mean

| Email / message | What it means | What to do |
|---|---|---|
| Run failed, log says **"Could not download"** | The website was down or slow for all 3 tries | Usually nothing. The backup run that night tries again. |
| Run failed, log says **"returned only N rows"** | The website probably changed its layout. Nothing was saved, so no junk got in. | Send the log text to Claude to fix the scraper. |
| Run failed, log says **"no data was saved for [date]"** | Today **was** saved, but yesterday was missed | Nothing to fix. That one day is lost because the site only shows today's numbers. |
| Run failed at **"Save the data into the repo"** with "403" or "Permission denied" | GitHub won't let the run save files | On the repository page, click the **Settings** tab, then **Actions**, then **General**, then *Workflow permissions*: choose **Read and write permissions**, click **Save**, then run it again (Step 5). |

To read a log, click **Actions**, then the run with the red X, then **snapshot**,
then the step with the red X.

---

## Getting the data out
- **Download the spreadsheet:** click the `data` folder, then `beds_daily.csv`, then the
  **download icon (⬇)** at the top right of the file view. It opens in Excel.
- **For the day-90 analysis:** download `beds_daily.csv` and upload it to Claude.

## Stopping
Collection stops itself after 90 days. To turn off the schedule completely, click
**Actions**, then **Daily bed snapshot**, then the **⋯** button (top right), then
**Disable workflow**. Your data stays in place.

---

## What's in the data (`beds_daily.csv`)

| Column | Meaning |
|---|---|
| `snapshot_date`, `snapshot_time_et` | When we captured it (Eastern time) |
| `feed` | `beds` (main page) or `recovery_housing` |
| `provider`, `provider_slug` | Facility name, plus a stable ID that doesn't change |
| `bed_type`, `service`, `setting`, `town` | What kind of bed and where. `town` is only filled in for recovery housing. |
| `status` | Open / Filled / Waiting |
| `available`, `filled`, `waiting` | The counts. `waiting` is blank for recovery housing. |
| `total_reported_beds` | available + filled (a rough stand-in for capacity) |
| `provider_updated_et` | When the provider last updated its numbers |
| `hours_since_provider_update` | How old that update was when we captured it |

**Two rules for analysis:**
1. **Providers update on their own schedule.** Some rows can be days or weeks old.
   Before charting trends, keep only "fresh" rows (`hours_since_provider_update` under 48),
   or count a provider only on days its update time changed.
2. **"ED (BH - Waiting)" rows aren't beds.** They count behavioral-health patients
   waiting in emergency departments. Analyze them separately.
