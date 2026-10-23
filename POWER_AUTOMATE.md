# Sending emails through Power Automate

The **Send** button posts the email to the app (`/api/send`), which passes it on to a
Power Automate flow. The flow sends it from your Outlook account:

```
When an HTTP request is received
  -> Condition: dry_run is true?
       True  -> Response 200 {"dry_run": true, ...}   (no email)
       False -> Send an email (V2) -> Response 200 {"sent": true}
```

## 1. Create the flow

1. Go to <https://make.powerautomate.com> → **Create → Create from blank** (new designer),
   name it `QA email sender`, and add the trigger **When an HTTP request is received**
   (Request connector; it's a Premium trigger).
2. In the trigger:
   - **Who can trigger the flow?** → **Anyone** (the URL carries its own secret key).
   - **Request Body JSON Schema** → paste this:

   ```json
   {
     "type": "object",
     "properties": {
       "to":              { "type": "string" },
       "cc":              { "type": "string" },
       "subject":         { "type": "string" },
       "html":            { "type": "string" },
       "text":            { "type": "string" },
       "instructor_id":   { "type": "string" },
       "instructor_name": { "type": "string" },
       "test":            { "type": "boolean" },
       "dry_run":         { "type": "boolean" }
     },
     "required": ["to", "subject", "html"]
   }
   ```

3. **+ → Control → Condition**. Left value (fx expression):
   `coalesce(triggerBody()?['dry_run'], false)`, operator **is equal to**, right value
   (fx expression): `true`.
4. **True** branch → **+ → Request → Response**: Status Code `200`, Headers
   `Content-Type: application/json`, Body:

   ```
   {"dry_run": true, "would_send_to": "@{triggerBody()?['to']}", "would_cc": "@{triggerBody()?['cc']}", "subject": "@{triggerBody()?['subject']}"}
   ```

   No email action goes in this branch.
5. **False** branch → **+ → Office 365 Outlook → Send an email (V2)**. Sign in with the
   mailbox the emails should come from. Fill it from dynamic content / expressions:
   - **To** → `to`
   - **Subject** → `subject`
   - **Body** → `html`. Switch the body box to code view (`</>`) first so the formatting comes through.
   - **Advanced → CC** → `cc`
   - Optional: **From (Send as)** → a shared mailbox address.
   Then **+ → Request → Response** after it: Status Code `200`, Body `{"sent": true}`.
6. **Save**. Open the trigger again and copy the **HTTP URL**. It's long and includes
   `sig=...`. Treat it like a password: anyone with it can send email as you.

## 2. Connect the app (Render → Environment)

| Variable | Value |
|---|---|
| `SEND_API_URL` | the HTTP URL from step 6 |
| `SEND_MODE` | `dryrun` (default), `test` or `live`, see below |
| `SEND_TEST_TO` | only for `test` mode: the address(es) that receive every email, comma-separated |

Save, and Render redeploys. The status bar at the top of the page shows the mode.

## 3. The three modes

- **dryrun** (the default): the app sends the email to the flow with `dry_run: true`
  and `[DRY RUN]` in the subject. The flow answers with what it received and **sends
  no email to anyone**. The page says *Dry run OK* only if the flow confirms it skipped
  sending. Use this to test the API connection.
- **test**: every email goes only to `SEND_TEST_TO`, with no CC, `[TEST]` in the subject
  and a yellow note saying who it would have gone to. Instructors and managers receive nothing.
- **live**: the email goes to the instructor, with the CC list (Always CC and the capability manager).

## 4. Check it works

1. Open the app, upload the export and pick an instructor.
2. Click **Send** and confirm. In dry-run mode the page should say *Dry run OK*.
3. In Power Automate, open the flow → **Run history**: the run is green, and the
   **True** branch ran (Send an email was skipped).

If it fails, the page shows Power Automate's error. Common causes:
- **401/403**: the URL was copied wrong or the trigger isn't set to *Anyone*. Some
  organisations block *Anyone*; then ask IT to allow it.
- **400**: the request doesn't match the schema in step 2. Paste it again exactly.
- **"didn't confirm it skipped sending"**: the flow has no dry_run Condition, or the
  True-branch Response body is missing `"dry_run": true`.
- **Body shows HTML tags**: the Body box wasn't in code view in step 5.
