# BigQuery exports for the QA app

Five read-only queries on `kossip-helpers.niat_instructor_efficacy_ai_analytics_workspace`, and a script
that runs them and writes the CSVs.

| # | Query | CSV | Page | One row per | Window |
|---|---|---|---|---|---|
| 1 | `1_qa_daily.sql` | `qa_daily.csv` | 2 | day × campus × subject × session type × instructor | 28 days before the 1st of the month → yesterday |
| 2 | `2_manager_map.sql` | `manager_map.csv` | 2 | instructor × manager | current |
| 3 | `3_manager_connects.sql` | `manager_connects.csv` | 2 | connect slot | full history → today |
| 4 | `4_manager_activity.sql` | `manager_activity.csv` | 2 | day × manager × event | same as 1, → today |
| 5 | `5_instructor_qa_last3_export.sql` | `instructor_qa_last3_export.csv` | 1 | instructor | each instructor's last 3 QA days (within 60 days; latest within 14) |

Every run writes `qa_exports/YYYYMMDD/` (history), `qa_exports/latest/` (same file names every day —
upload these) and `qa_exports/qa_exports_YYYYMMDD.zip`.

```
python3 run_exports.py          # all five
python3 run_exports.py 5        # only the page-1 file
python3 run_exports.py 1 2 3 4  # only the page-2 files
```

## Option A — Cloud Shell, one command each morning

First time:
1. Cloud Shell → ⋮ menu → **Upload** → `bigquery.zip`
2. `unzip -o bigquery.zip`

Every morning:
```
bash ~/bigquery/daily_cloudshell.sh
```
It runs all five queries and downloads the zip to your computer. Cloud Shell can't run on a schedule
by itself (it switches off when you close it), so this one is a daily click.

## Option B — automatic every morning on your Windows laptop

First time:
1. Install the Google Cloud SDK (includes `bq`): https://cloud.google.com/sdk/docs/install — tick "add to PATH".
2. In a new Command Prompt:
   ```
   gcloud auth login
   gcloud config set project kossip-helpers
   bq query --nouse_legacy_sql "SELECT 1"
   ```
   (if your queries are billed to a different project, set that project instead, or set the
   `QA_BQ_PROJECT` environment variable)
3. Test: double-click `bigquery\daily_windows.bat`, then open `exports\daily_log.txt`.
4. Schedule it for 8:00 every day (Command Prompt, one line, change the path if needed):
   ```
   schtasks /Create /TN "QA daily export" /SC DAILY /ST 08:00 /TR "C:\Users\Nxtwave\Desktop\Projects\QA_report\bigquery\daily_windows.bat"
   ```
   The laptop has to be on and signed in at that time; if it was off, run the .bat by hand.

CSVs land in `QA_report\exports\latest\`. They contain instructor names and emails, so keep
`exports/` out of git (add it to `.gitignore`).

## Rules shared by all queries
SCHEDULE evaluations only (CONNECT excluded), completed, latest report per session section, report
must belong to the scheduled instructor, LECTURE and PRACTICE scores only, a 0 with no rubric rows is
not a score, IDs lower-case without hyphens, missing values left empty (never 0).

Query 4 is a template: `run_exports.py` reads the event view's columns and fills it in
(saved as `4_manager_activity.filled.sql`). The other four can be pasted into the BigQuery console as is.
