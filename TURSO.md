# Saving the daily data in Turso (both pages)

With a Turso database connected, both pages save every upload and open on the saved data. You upload
the daily zip once a day, on either page, and anyone opening either page sees the latest data.
Page 1 uses the zip's `instructor_qa_last3_export.csv` and page 2 uses the other four files.

## How the data is kept
- **The upload overrides the days it covers.** The daily zip covers about 50 days. For QA, connects
  and platform activity, the saved rows for those days are replaced with the upload's rows, so any
  status or score that changed overnight is updated. Days outside the upload stay as they were.
- **Only the last 30 days are kept**, counted back from the latest QA day. Set `KEEP_DAYS` to change
  this (minimum 28, because the analysis compares 14 days with the 14 before).
- **A few history rows are kept for connects older than that.** For each manager–instructor pair, the
  database keeps the first connect, the last connect and the last completed connect. For each manager,
  it keeps the last day they used the platform. This keeps lines like "3 months without a recorded
  connect" true.
- **The manager map is replaced by each upload.** The exception is an export older than the saved one,
  which doesn't replace it.
- **Page 1's file is saved whole** (gzipped, about 0.7 MB). Each upload replaces it, unless the
  upload comes from an older export than the saved one. Uploading page 1's CSV on its own saves only
  that file.
- **Only changed rows are written.** Uploading the same zip twice writes nothing. A normal day writes a
  few thousand rows, which is well inside Turso's free plan.

**What 30 days changes:** the month view only has watchlist history for the last few days of the
month, and it compares the month with fewer days before it. A page check says so. Use `KEEP_DAYS=60`
for the full month view.

## Setup (about 5 minutes, in the browser)
1. Go to <https://app.turso.tech> and sign up. You can use GitHub or Google.
2. **Create Database.** Name it `qa-report`. Pick the location closest to your Render service's region.
3. Open the database and copy its **URL**. It looks like `libsql://qa-report-<you>.turso.io`.
4. Click **Create Token** (or **Generate Token**). Choose **Read & Write** and no expiry, then copy the token.
   Treat it as a password: don't paste it in chats or commit it.
5. In Render, open the service, go to **Environment**, and add:
   - `TURSO_DATABASE_URL` = the URL from step 3
   - `TURSO_AUTH_TOKEN` = the token from step 4
   - `KEEP_DAYS` = `30` (optional; 30 is the default)
6. Save. Render redeploys. Then open **Manager analysis** and upload today's zip once.
   - The toast shows how many rows were added and removed.
   - The first check line says "Saved data: QA from … to …".
   - From then on, the page opens on the saved data.

The app creates its tables on the first upload. The tables are `qa_daily`, `manager_map`,
`manager_connects`, `manager_activity`, `files` (page 1's file), `meta` and `uploads` (a log of the last 60 uploads). You can browse
them in Turso's web console.

To test locally without Turso, set `TURSO_DATABASE_URL` to a file path such as `qa.db`. A local SQLite
file is used instead.
