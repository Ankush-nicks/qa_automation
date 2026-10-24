# Sending emails through Resend

The **Send** button sends the email through [Resend](https://resend.com). The Google
Sheet script (`GOOGLE_SHEETS.md`) is still used for the **Sent log** and the 7-day repeat
check, but Gmail isn't needed: the script only checks and logs.

```
Send ─► app ─► Google Sheet: is this a repeat?  ─► Resend sends the email ─► Google Sheet: log the row
```

## 1. Render → Environment

| Variable | Value |
|---|---|
| `RESEND_API_KEY` | your Resend API key (`re_…`). Keep it only here, never in the code |
| `RESEND_FROM` | the sender, e.g. `Central QA Team <qa@nxtwave.co.in>`. Its domain must be verified in Resend (step 2). Until then leave it unset: it defaults to `Central QA Team <onboarding@resend.dev>` |
| `RESEND_REPLY_TO` | optional: where replies go, e.g. your Outlook address, so instructors' replies land in Outlook |
| `SEND_API_URL` | the Google Sheet script's `/exec` URL (for the log and repeat check) |
| `SEND_SECRET` | the script's `SECRET` |
| `SEND_MODE` | `dryrun` (default), `test` or `live` |
| `SEND_TEST_TO` | test mode only: who receives the test emails |

When `RESEND_API_KEY` is set, Resend does the sending and the script at `SEND_API_URL`
only logs. The status bar shows **Sending via Resend: … · logged in the Google Sheet**.

## 2. Verify the domain (needed before emailing anyone else)

Until a domain is verified, Resend only delivers to **the email address the Resend account was
created with** (for this account, `instructor.central@nxtwave.co.in`). Every other recipient is
refused with "You can only send testing emails to your own email address".

1. In Resend: **Domains → Add domain**. Use a subdomain such as `qa.nxtwave.co.in`, so the main
   domain's email setup isn't touched.
2. Resend shows a few DNS records (SPF/MX and DKIM, optionally DMARC). Ask whoever manages
   nxtwave.co.in's DNS (IT) to add them exactly as shown.
3. Click **Verify** once they're added. It can take from a few minutes to a few hours.
4. Set `RESEND_FROM` in Render to an address on that domain, e.g.
   `Central QA Team <qa@qa.nxtwave.co.in>`. The address doesn't need a real mailbox, but set
   `RESEND_REPLY_TO` so replies reach someone.

## 3. Update the Google Sheet script

The script needs the new "check" and "log" actions:

1. In Apps Script, replace everything in `Code.gs` with the latest `google_apps_script/Code.gs`
   and click **Save**.
2. **Deploy → Manage deployments → ✏️ Edit → Version: New version → Deploy**. The URL stays the same.

## 4. Test

1. **Dry run** (`SEND_MODE` unset): **Send** → *Dry run OK*. Resend isn't called; the sheet gets
   a "Dry run (not sent)" row.
2. **Test** (`SEND_MODE=test`, `SEND_TEST_TO`=your address): **Send** → the test inbox receives a
   `[TEST]` email and the sheet gets a `test` / `Sent` row with the Resend id. Before the domain is
   verified, `SEND_TEST_TO` must be the Resend account's own address.
3. **Live** (`SEND_MODE=live`) once the domain is verified.

Every email also appears in Resend under **Emails**, with its delivery status (delivered, bounced…).
