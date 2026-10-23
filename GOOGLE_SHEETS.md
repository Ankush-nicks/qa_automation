# Sending emails through Google Sheets (Gmail)

The **Send** button posts the email to a small Google Apps Script attached to a
Google Sheet. The script:

1. **sends the email** from the Google Workspace account that sets it up (Gmail, with the
   formatting, To and CC);
2. **logs every email** as a row in the sheet's **Sent log** tab: date, mode, status,
   instructor, subject area, institute, daily averages, To, CC and subject;
3. **stops repeat emails**: a live email to an instructor already emailed (live) in the last
   7 days is refused until you confirm it on the page. The page also tags those instructors
   **Emailed 22 Oct**.

The script's code is in `google_apps_script/Code.gs`.

## 1. Create the sheet and the script

1. Sign in to Google with the **@nxtwave.co.in account that should send the emails**. A shared
   QA mailbox account is best. Create a new Google Sheet named `QA email log`.
2. In the sheet: **Extensions → Apps Script**. Delete what's in `Code.gs`, paste in the whole
   contents of `google_apps_script/Code.gs` from this repo, and click **Save**.
3. **Project Settings** (gear icon) → **Script properties → Add script property**:
   - Property: `SECRET`
   - Value: a long random password, e.g. 30+ letters and numbers. You'll put the same value
     in Render. Don't reuse a real password.
4. **Deploy → New deployment** → type **Web app**:
   - Description: `QA email sender`
   - **Execute as: Me**. Emails are sent from this account.
   - **Who has access: Anyone**. The SECRET protects it, so only the QA app can use it.
   - Click **Deploy**, then **Authorize access** and allow Gmail and Sheets. If Google warns
     "Google hasn't verified this app", that's expected for your own script: **Advanced → Go to
     … (unsafe)**.
5. Copy the **Web app URL**. It ends in `/exec`.

The **Sent log** tab is created automatically with the first email.

## 2. Connect the app (Render → Environment)

| Variable | Value |
|---|---|
| `SEND_API_URL` | the Web app URL from step 5 |
| `SEND_SECRET` | the same value as the script's `SECRET` |
| `SEND_MODE` | `dryrun` (default), `test` or `live`, see below |
| `SEND_TEST_TO` | only for `test` mode: the address(es) that receive every email |
| `SEND_REPEAT_DAYS` | optional, default `7`: how many days count as a repeat |

Save, and Render redeploys. The status bar shows **Sending via Google Sheets: …** and the mode.

## 3. The three modes

- **dryrun** (default): nothing is emailed. The script checks the secret and logs a
  "Dry run (not sent)" row. Use it to check the connection.
- **test**: every email goes only to `SEND_TEST_TO`, with no CC, `[TEST]` in the subject and a
  yellow note saying who it would have gone to. Logged as mode `test`.
- **live**: the email goes to the instructor with the CC list.

Only **live** emails count for the repeat check. Dry runs and test emails never block anyone.

## 4. Check it works

1. With `SEND_MODE=dryrun`: open the app, upload the export, pick an instructor, click **Send**.
   The page should say *Dry run OK*, and the sheet should get a "Dry run (not sent)" row.
2. With `SEND_MODE=test` and `SEND_TEST_TO` set: **Send** again. The test inbox should receive the
   email with headings and bold, and the sheet gets a `test` / `Sent` row.
3. When you're happy, set `SEND_MODE=live`.

## Changing the script later

After editing `Code.gs`: **Deploy → Manage deployments → ✏️ Edit → Version: New version →
Deploy**. This keeps the same URL. A *New deployment* would create a new URL instead.

## Limits and notes

- Google Workspace accounts can email about **1,500 recipients a day** through Apps Script.
  CC counts, so that's roughly 500 emails a day at 3 recipients each.
- The log is a normal sheet: you can sort, filter and add your own columns to the right of
  **Note**. Don't reorder or rename the first 13 columns, because the repeat check reads them.
- If sending fails, the page shows the reason, and the sheet gets a `Failed` row with the error
  in **Note**.
- **"answered with a web page instead of JSON"**: the deployment isn't set to *Who has access:
  Anyone*, or the URL isn't the `/exec` one.
- **"Wrong or missing secret"**: `SEND_SECRET` in Render doesn't match the script's `SECRET`.
