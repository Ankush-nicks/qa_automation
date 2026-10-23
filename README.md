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
- `google_apps_script/Code.gs` – the Google Apps Script that sends emails from Gmail and logs them in a Google Sheet (setup: `GOOGLE_SHEETS.md`)
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
   - `SEND_SECRET` – required for Google Sheets: the same value as the script's `SECRET` property
   - `SEND_REPEAT_DAYS` – optional, default `7`. A live email to an instructor already emailed (live) in
     this many days is refused by the script until it's confirmed on the page
   - `SEND_MODE` – `dryrun` (default), `test` or `live`. Dry run sends the request with
     `dry_run: true` and no email is sent (for testing the connection). In test mode every email
     goes only to `SEND_TEST_TO`, with no CC, `[TEST]` in the subject and a note of who it would have gone to
   - `SEND_TEST_TO` – the address(es) that receive emails in test mode (required for test mode only)
   - `APP_PASSWORD` – optional but recommended; the browser will ask for it (any username)
   - To use Claude instead: `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY`; `ANTHROPIC_MODEL` is optional and defaults to `claude-haiku-5-5`
5. Deploy and open the URL.

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
