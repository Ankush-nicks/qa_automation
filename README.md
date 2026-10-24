# QA email drafts

Upload the instructor QA export (last 7 or 30 days). Each day's average is worked
out from that day's session scores, and days without reviews are skipped. The app
flags instructors whose daily average is below the threshold (default 5) on each of
their last 3 days with QA reviews (each day is checked on its own, not the overall
average). Instructors with fewer than 3 QA days aren't flagged. It shows their scores and rubric categories
(lecture and practice sessions), and writes each instructor's email with an LLM
using their scores and the updated rubric workbook. If the AI isn't set up or
fails, a rule-based draft is always available.

## Files
- `app.py` – Flask web app (routes, password, rubric upload)
- `qa_engine.py` – reads the export, flags instructors, builds the facts and the rule-based draft
- `managers.py` – reads the capability manager list and finds each instructor's manager among the names in the export's `instructor_manager_name` column (column F)
- `roster.py` – reads the optional roster export (another way to map instructors to capability manager emails)
- `rubrics.py` – reads the rubric workbook and pulls sub-rubric definitions and score bands
- `llm.py` – writes the email with OpenAI gpt-4o-mini (default) or Claude, and checks the numbers it used
- `templates/index.html` – the page
- `google_apps_script/Code.gs` – the Google Apps Script that sends emails (from Gmail, or from Outlook through Microsoft Graph) and logs them in a Google Sheet (setup: `GOOGLE_SHEETS.md`, `OUTLOOK.md`)
- `bundle.py` – page 2: reads the daily BigQuery zip (or its CSVs), checks files, columns and dates
- `manager_engine.py` – page 2: campus analysis, watchlists, instructor last-3-QA-day averages, Capability Managers and connects, month view
- `store.py` – page 2: saves each upload in a Turso database and keeps the last `KEEP_DAYS` days (`TURSO.md`)
- `daily_email.py`, `templates/daily_email.html` – page 2: the approved daily email (HTML + plain text)
- `templates/manager.html` – page 2 (`/manager`)
- `bigquery/` – the five BigQuery export queries and the daily scripts (see `bigquery/README.md`)
- `rubrics/Updated_Rubrics_and_Scoring.xlsx` – the rubric workbook the AI uses
- `capability_managers.xlsx` – capability manager names and emails (columns `Capability Manager Name`, `Capability Manager Email`). To change it, edit the file and push

## Deploy on Render (Web Service)
1. Push this folder to a GitHub repository (keep the folder structure).
2. In Render choose **New → Web Service** and connect the repository
   (or **New → Blueprint** to use `render.yaml`).
3. Settings:
   - Runtime: Python 3
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --workers 2 --threads 4 --timeout 180 --bind 0.0.0.0:$PORT`
4. Environment variables:
   - `OPENAI_API_KEY` – required for AI emails
   - `OPENAI_MODEL` – optional, defaults to `gpt-4o-mini` (fast)
   - `CC_EMAILS` – optional, comma-separated addresses to CC on every email; defaults to Prudhvi
     (`meka.prudhvicharan@nxtwave.co.in`). Can also be changed in the page's "Always CC" field.
     Each email also CCs the instructor's capability manager: the name in column F that appears in
     `capability_managers.xlsx`. If none matches, the export's `capability_manager_email` column is used
   - `CC_ALLOWED_DOMAINS` – optional, defaults to `nxtwave.co.in,nxtwave.tech`. Capability-manager addresses
     outside these domains are shown on the page but not CC'd
   - `SEND_API_URL` – optional; where the **Send** button sends the email. Recommended: the Google Apps
     Script web app (`GOOGLE_SHEETS.md`), which sends from Gmail, logs every email in a Google Sheet and
     refuses repeat emails. A Power Automate flow (`POWER_AUTOMATE.md`) also works. The app POSTs JSON
     `{to, cc, subject, html, text, instructor_id, instructor_name, instructor_email, subject_area,
     institute, scores, sender_name, test, dry_run, force, repeat_days, secret}`; `to` and `cc` are
     `;`-separated. `SEND_API_TOKEN`, if set, goes in an `Authorization: Bearer` header (Power Automate).
     Until it's set, Send says sending isn't set up and Copy email still works
   - `RESEND_API_KEY` – when set, emails are sent through Resend (`RESEND.md`) and the Google Sheet script
     at `SEND_API_URL` is used only for the log and the repeat check. `RESEND_FROM` sets the sender (its
     domain must be verified in Resend; default `onboarding@resend.dev`, which only delivers to the Resend
     account's own address) and `RESEND_REPLY_TO` where replies go
   - `SEND_SECRET` – required for Google Sheets: the same value as the script's `SECRET` property
   - `SEND_REPEAT_DAYS` – optional, default `7`. A live email to an instructor already emailed (live) in
     this many days is refused by the script until it's confirmed on the page
   - `SEND_MODE` – `dryrun` (default), `test` or `live`. Dry run sends the request with
     `dry_run: true` and no email is sent (for testing the connection). In test mode every email
     goes only to `SEND_TEST_TO`, with no CC, `[TEST]` in the subject and a note of who it would have gone to
   - `SEND_TEST_TO` – the address(es) that receive emails in test mode (required for test mode only)
   - `DAILY_TO` – page 2: who receives the daily email (QA leads, comma-separated). The Capability Managers in
     `capability_managers.xlsx` are CC'd. It uses the same sender, `SEND_MODE` and Sent log as page 1
   - `APP_URL` – optional: the app's address for the email's "Open the full analysis" button (defaults to the
     address the page was opened from)
   - `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN` – optional: page 2 saves each upload in this Turso database
     and opens on the saved data (`TURSO.md`). Unset = nothing is stored and the zip is needed every time
   - `KEEP_DAYS` – optional, default `30` (minimum 28): how many days of QA data the database keeps
   - `APP_PASSWORD` – optional but recommended; the browser will ask for it (any username)
   - To use Claude instead: `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`; `ANTHROPIC_MODEL` is optional and defaults to `claude-haiku-5-5`
5. Deploy and open the URL.

## Page 2: Manager analysis (`/manager`)
Every morning:
1. Cloud Shell: `bash ~/bigquery/daily_cloudshell.sh` downloads `qa_exports_YYYYMMDD.zip` (5 CSVs).
2. Open **Manager analysis** and drop the zip. The app checks the four page-2 files (columns, date ranges) and
   shows the checks at the top. With Turso set up the upload is saved: it replaces the days it covers, only the
   last `KEEP_DAYS` days are kept, and the page opens on the saved data next time (no upload needed). Without
   Turso nothing is stored.
3. The report date defaults to the latest day in `qa_daily.csv` (yesterday); the month view to its month.
   Changing either re-runs the analysis.
4. Tabs: Act on these today · Campus analysis · Manager connect usage · Manager detail · Month view · Daily email.
   Tables download as CSV.
5. **Daily email**: Preview, Copy HTML (pastes with formatting into Outlook/Gmail) or Send. Send follows
   `SEND_MODE` (dry run → test → live) and logs to the Sent log; a second live send for the same report date
   asks for confirmation.
6. Page 1 accepts the same zip (it uses `instructor_qa_last3_export.csv` inside it).

Rules: averages are SUM(score_sum) ÷ SUM(scored) with their counts, never an average of averages; missing data
shows as "no data", never 0. Connects on the report date are provisional, so managers are judged on the days
before. Managers with fewer than 3 instructors are listed under the table. A score change after a connect is an
association, not proof of effect.

## Run locally
```
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
python app.py          # http://localhost:5000
```

## Notes
- A rubric workbook uploaded from the page replaces the bundled one only until
  the service restarts or redeploys. To change it permanently, replace the file
  in `rubrics/` and push.
- Uploaded exports are processed in memory and not stored. When you write an
  email, that instructor's scores (and rubric text) are sent to the LLM provider.
- Every decimal in an AI draft is checked against the data; anything that
  doesn't match is shown as a warning above the email.
