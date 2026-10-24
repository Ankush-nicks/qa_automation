# Sending from Outlook (through the Google Sheet script)

The Google Sheet script (`GOOGLE_SHEETS.md`) can send through **Outlook** instead of Gmail.
It uses Microsoft Graph, Microsoft's mail API, so emails come from your Microsoft 365 address
and land in its **Sent Items**. The app, the Sent log and the repeat check work the same way.

```
Send ─► app ─► Google Sheet script ─► Microsoft Graph ─► email from your Outlook
                    └─► Sent log row
```

This needs a one-time **app registration** in Microsoft Entra. If your organisation blocks
users from registering apps or from allowing "send mail as you", it can't work without an admin.

## 1. Register the app (Microsoft Entra)

1. Go to <https://entra.microsoft.com> and sign in with the Outlook account that should send.
2. **Applications → App registrations → + New registration**:
   - Name: `QA email sender`
   - Supported account types: **Accounts in this organizational directory only**
   - Redirect URI: **Web**, with the script's callback address:
     `https://script.google.com/macros/d/<SCRIPT_ID>/usercallback`
     (`<SCRIPT_ID>` is in Apps Script → Project Settings → IDs)
   - **Register**
3. From **Overview**, copy the **Application (client) ID** and **Directory (tenant) ID**.
4. **Certificates & secrets → Client secrets → + New client secret** → copy the **Value**
   (not the Secret ID). It's shown only once.
5. **API permissions → + Add a permission → Microsoft Graph → Delegated permissions** →
   tick **Mail.Send** → **Add permissions**. (`User.Read` is usually there already.)

## 2. Set up the script

1. Apps Script → **Libraries → +** → Script ID
   `1B7FSrk5Zi6L1rSxxTDgDEUsPzlukDsi4KGuTMorsTQHhGBzBkMun4iDF` → **Look up** → keep the
   latest version and the identifier **OAuth2** → **Add**.
2. Paste the latest `google_apps_script/Code.gs` and **Save**.
3. **Project Settings → Script properties**, add:

| Property | Value |
|---|---|
| `MAIL_SENDER` | `outlook` |
| `MS_CLIENT_ID` | the Application (client) ID |
| `MS_TENANT_ID` | the Directory (tenant) ID |
| `MS_CLIENT_SECRET` | the client secret **Value** |

   Keep `SECRET` as it is.
4. **Deploy → Manage deployments → ✏️ Edit** the web app → **Version: New version** → **Deploy**.

## 3. Sign in to Outlook once

1. In the editor, choose **connectOutlook** in the function list and click **Run**. Allow the
   new permission Google asks for ("connect to an external service").
2. The **Execution log** shows a link. Open it, sign in with the Outlook account and **Accept**.
3. The page should say **Outlook is connected**.
   - **"Need admin approval"**: your organisation doesn't let users allow this. Set
     `MAIL_SENDER` back to `gmail` (or delete it) to keep sending through Gmail.

The sign-in is remembered. To switch accounts, run **disconnectOutlook**, then **connectOutlook**.

## 4. Test

With `SEND_MODE=test` in Render, click **Send** in the app. The test inbox should get the
`[TEST]` email from your Outlook address, it should appear in that account's **Sent Items**, and
the Sent log row's **Note** says **Sent from Outlook**.

## Notes

- The client secret expires (6 to 24 months, chosen in step 1.4). When it does, sending fails
  with a sign-in error: create a new secret, update `MS_CLIENT_SECRET` and run **connectOutlook**.
- Microsoft 365 mailboxes can send to about 10,000 recipients a day and 30 messages a minute.
