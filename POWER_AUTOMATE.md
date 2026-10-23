# Sending emails through Power Automate

The **Send** button posts the email to a Power Automate flow, which sends it from
your Outlook account. The flow has three steps:

```
When an HTTP request is received  ->  Send an email (V2)  ->  Response
```

## 1. Create the flow

1. Go to <https://make.powerautomate.com>, then **Create → Instant cloud flow**
   (or **Automated** / **Blank**), name it `QA email sender`, and pick the trigger
   **When an HTTP request is received**. Click **Create**.
2. Open the trigger:
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
       "test":            { "type": "boolean" }
     },
     "required": ["to", "subject", "html"]
   }
   ```

3. **+ New step → Office 365 Outlook → Send an email (V2)**. Sign in with the
   mailbox the emails should come from (for example a shared QA mailbox you can send as).
   Fill it in from **Dynamic content**:
   - **To** → `to`
   - **Subject** → `subject`
   - **Body** → `html`. The body box must be in HTML/code view (the `</>` button)
     so the headings and bold come through.
   - **Show advanced options → CC** → `cc`
   - Optional: **From (Send as)** → the shared mailbox address, if you send from one.
4. **+ New step → Request → Response**. Set **Status Code** to `200`. Leave the body empty.
5. **Save**. Open the trigger again and copy the **HTTP POST URL**. It's long and
   includes `sig=...`. Treat it like a password: anyone with it can send email as you.

## 2. Connect the app (Render → Environment)

| Variable | Value |
|---|---|
| `SEND_API_URL` | the HTTP POST URL from step 5 |
| `SEND_MODE` | `test` for now (this is also the default). Change it to `live` when you're ready |
| `SEND_TEST_TO` | the address that receives every email in test mode, e.g. your own work email. Several are allowed, comma-separated |

Save, and Render redeploys. The status bar at the top of the page then shows
**Sending: test mode, every email goes to …**.

## 3. Test mode vs live

- **Test mode** (the default): every email goes only to `SEND_TEST_TO`. The CC list is
  left out and the subject starts with `[TEST]`. A yellow note at the top of the email
  says who it would have gone to (To and CC) in live mode. Instructors and managers
  receive nothing.
- **Live** (`SEND_MODE=live`): the email goes to the instructor, with the CC list
  (Always CC and the capability manager).

## 4. Check it works

1. Open the app, upload the export and pick an instructor.
2. Click **Send** and confirm. The page should say *Test email sent to …*.
3. In Power Automate, open the flow and check **Run history**: the run should be green.
4. Check the inbox of `SEND_TEST_TO`. The email should have the headings and bold.

If it fails, the page shows Power Automate's error. Common causes:
- **401/403**: the URL was copied wrong or the trigger isn't set to *Anyone*. Some
  organisations block *Anyone*; then ask IT to allow it, or set `SEND_API_TOKEN` and use the
  tenant-only option.
- **400**: the schema in step 2 doesn't match. Paste it again exactly.
- **Body shows HTML tags**: the Body box wasn't in HTML/code view in step 3.
