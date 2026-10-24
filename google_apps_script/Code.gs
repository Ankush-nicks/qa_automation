/**
 * QA email log: a Google Apps Script web app attached to a Google Sheet.
 * Every email the QA app sends is logged in the "Sent log" tab, which is also how
 * repeat emails are caught. When the app sends through Resend it only asks this
 * script to "check" (is it a repeat?) and "log" (write the row). Without an action,
 * the script sends the email itself: through Gmail, or through Outlook (Microsoft Graph)
 * when MAIL_SENDER is "outlook".
 * Setup steps: GOOGLE_SHEETS.md (Gmail) and OUTLOOK.md (Outlook).
 *
 * Script properties (Project Settings > Script properties):
 *   SECRET         - the same value as SEND_SECRET on the QA app's server (required)
 *   MAIL_SENDER    - "outlook" to send from Outlook; anything else (or unset) sends through Gmail
 *   MS_CLIENT_ID, MS_TENANT_ID, MS_CLIENT_SECRET - the Microsoft app registration (Outlook only)
 * Outlook also needs the OAuth2 library (Libraries > +, script ID
 * 1B7FSrk5Zi6L1rSxxTDgDEUsPzlukDsi4KGuTMorsTQHhGBzBkMun4iDF, identifier OAuth2), and one run
 * of connectOutlook() from the editor to sign in.
 */

const LOG_SHEET = 'Sent log';
const HEADERS = ['Sent at', 'Mode', 'Status', 'Instructor ID', 'Instructor', 'Instructor email', 'Subject area',
                 'Institute', 'Daily averages', 'To', 'CC', 'Subject', 'Note'];
const DEFAULT_REPEAT_DAYS = 7;

/** POST: send (or dry-run) one email and log it. Always answers 200 with JSON. */
function doPost(e) {
  let body;
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return json_({ ok: false, error: 'The request body was not valid JSON.' });
  }
  if (!checkSecret_(body.secret)) return json_({ ok: false, error: secretError_(body.secret) });
  // The app sends the email itself (e.g. through Resend) and uses this sheet only for the
  // repeat check and the log
  if (body.action === 'check' || body.action === 'log') return logAction_(body);

  const to = addresses_(body.to);
  const cc = addresses_(body.cc);
  const subject = String(body.subject || '').trim();
  if (!to || !subject || !(body.html || body.text)) {
    return json_({ ok: false, error: 'The email needs a recipient, a subject and a body.' });
  }
  const mode = body.dry_run ? 'dryrun' : body.test ? 'test' : 'live';
  const repeatDays = Number(body.repeat_days) > 0 ? Number(body.repeat_days) : DEFAULT_REPEAT_DAYS;

  const lock = LockService.getScriptLock();
  lock.waitLock(30000); // one send at a time, so two clicks can't both pass the repeat check
  try {
    const sheet = logSheet_();
    const who = { id: normId_(body.instructor_id), email: String(body.instructor_email || '').trim().toLowerCase() };

    // Repeat check: only live emails count, since test and dry-run emails never reach the instructor
    if (mode === 'live' && !body.force) {
      const prev = lastLiveSend_(sheet, who, repeatDays);
      if (prev) {
        return json_({ ok: false, duplicate: true, last_sent: prev.toISOString(), repeat_days: repeatDays,
                       error: 'This instructor was already emailed in the last ' + repeatDays + ' days.' });
      }
    }

    const now = new Date();
    const row = [now, mode, '', body.instructor_id || '', body.instructor_name || '', who.email,
                 body.subject_area || '', body.institute || '', body.scores || '', to, cc, subject, ''];

    if (mode === 'dryrun') {
      row[2] = 'Dry run (not sent)';
      sheet.appendRow(row);
      return json_({ ok: true, dry_run: true, would_send_to: to, would_cc: cc, subject: subject });
    }

    const outlook = useOutlook_();
    try {
      if (outlook) {
        sendOutlook_(to, cc, subject, String(body.html || ''));
      } else {
        const options = { htmlBody: String(body.html || ''), name: String(body.sender_name || 'Central QA Team') };
        if (cc) options.cc = cc;
        GmailApp.sendEmail(to, subject, String(body.text || ''), options);
      }
    } catch (err) {
      row[2] = 'Failed';
      row[12] = String(err && err.message || err);
      sheet.appendRow(row);
      return json_({ ok: false, error: (outlook ? 'Outlook' : 'Gmail') + ' could not send the email: ' + row[12] });
    }
    row[2] = body.force ? 'Sent again (confirmed)' : 'Sent';
    row[12] = outlook ? 'Sent from Outlook' : 'Sent from Gmail';
    sheet.appendRow(row);
    const out = { ok: true, sent: true, mode: mode, sent_at: now.toISOString(), sender: outlook ? 'outlook' : 'gmail' };
    if (!outlook) out.quota_left = MailApp.getRemainingDailyQuota();
    return json_(out);
  } finally {
    lock.releaseLock();
  }
}

/** GET ?action=recent&days=7&secret=... : the live emails sent in the last N days (for the app's "Emailed" tags). */
function doGet(e) {
  const p = (e && e.parameter) || {};
  if (!checkSecret_(p.secret)) return json_({ ok: false, error: secretError_(p.secret) });
  if (p.action !== 'recent') {
    const info = { ok: true, service: 'QA email sender', sender: useOutlook_() ? 'outlook' : 'gmail' };
    if (useOutlook_()) info.outlook_connected = outlookConnected_();
    return json_(info);
  }
  const days = Number(p.days) > 0 ? Number(p.days) : DEFAULT_REPEAT_DAYS;
  const since = new Date(Date.now() - days * 24 * 3600 * 1000);
  const rows = logRows_(logSheet_()).filter(function (r) { return isLiveSent_(r) && r.at >= since; });
  return json_({ ok: true, days: days, sent: rows.map(function (r) {
    return { instructor_id: r.id, instructor: r.name, email: r.email, sent_at: r.at.toISOString() };
  }) });
}

/**
 * action "check": is this a repeat? Answers {ok:true} or {ok:false, duplicate:true, last_sent}.
 * action "log":   appends one row with body.status (e.g. "Sent", "Failed") and body.note.
 */
function logAction_(body) {
  const mode = body.dry_run ? 'dryrun' : body.test ? 'test' : 'live';
  const repeatDays = Number(body.repeat_days) > 0 ? Number(body.repeat_days) : DEFAULT_REPEAT_DAYS;
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const sheet = logSheet_();
    if (body.action === 'check') {
      if (mode === 'live' && !body.force) {
        const who = { id: normId_(body.instructor_id), email: String(body.instructor_email || '').trim().toLowerCase() };
        const prev = lastLiveSend_(sheet, who, repeatDays);
        if (prev) {
          return json_({ ok: false, duplicate: true, last_sent: prev.toISOString(), repeat_days: repeatDays,
                         error: 'This instructor was already emailed in the last ' + repeatDays + ' days.' });
        }
      }
      return json_({ ok: true });
    }
    const now = new Date();
    sheet.appendRow([now, mode, String(body.status || 'Sent'), body.instructor_id || '', body.instructor_name || '',
                     String(body.instructor_email || '').trim().toLowerCase(), body.subject_area || '',
                     body.institute || '', body.scores || '', addresses_(body.to), addresses_(body.cc),
                     String(body.subject || ''), String(body.note || '')]);
    return json_({ ok: true, logged: true, logged_at: now.toISOString() });
  } finally {
    lock.releaseLock();
  }
}

// ---------------------------------------------------------------- Outlook --
// Sends from the Microsoft 365 account that signs in once with connectOutlook(), through
// Microsoft Graph (POST /me/sendMail). The sign-in is kept as a refresh token in the
// script properties, so it keeps working without signing in again.
function useOutlook_() {
  return String(PropertiesService.getScriptProperties().getProperty('MAIL_SENDER') || '').trim().toLowerCase() === 'outlook';
}

function outlookService_() {
  const p = PropertiesService.getScriptProperties();
  const need = function (k) {
    const v = String(p.getProperty(k) || '').trim();
    if (!v) throw new Error('The script property ' + k + ' is missing (see OUTLOOK.md).');
    return v;
  };
  if (typeof OAuth2 === 'undefined') {
    throw new Error('The OAuth2 library is not added to the script (Libraries > +, see OUTLOOK.md).');
  }
  const base = 'https://login.microsoftonline.com/' + need('MS_TENANT_ID') + '/oauth2/v2.0/';
  return OAuth2.createService('outlook')
    .setAuthorizationBaseUrl(base + 'authorize')
    .setTokenUrl(base + 'token')
    .setClientId(need('MS_CLIENT_ID'))
    .setClientSecret(need('MS_CLIENT_SECRET'))
    .setCallbackFunction('outlookCallback')
    .setPropertyStore(p)
    .setScope('offline_access https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/User.Read')
    .setParam('prompt', 'select_account');
}

function outlookConnected_() {
  try { return outlookService_().hasAccess(); } catch (err) { return false; }
}

/** Run this once from the editor (Run > connectOutlook), then open the link from the execution log. */
function connectOutlook() {
  const svc = outlookService_();
  if (svc.hasAccess()) {
    Logger.log('Outlook is already connected. To use another account, run disconnectOutlook first.');
    return;
  }
  Logger.log('Open this link, sign in with the Outlook account that should send the emails, and accept:\n%s',
             svc.getAuthorizationUrl());
}

/** Forgets the Outlook sign-in (run connectOutlook again afterwards). */
function disconnectOutlook() {
  outlookService_().reset();
  Logger.log('Outlook sign-in removed.');
}

/** Microsoft sends the browser back here after the sign-in. */
function outlookCallback(request) {
  let ok = false, detail = '';
  try {
    ok = outlookService_().handleCallback(request);
  } catch (err) {
    detail = String(err && err.message || err);
  }
  const msg = ok ? 'Outlook is connected. You can close this tab.'
                 : 'Outlook was not connected. ' + (detail || 'The sign-in was cancelled or refused.') +
                   ' If Microsoft asked for admin approval, your organisation does not let users allow this.';
  return HtmlService.createHtmlOutput('<p style="font-family:Arial,sans-serif">' +
                                      msg.replace(/[<>&]/g, function (c) { return { '<': '&lt;', '>': '&gt;', '&': '&amp;' }[c]; }) +
                                      '</p>');
}

/** to and cc are comma-separated. Throws with Microsoft's reason when the email isn't accepted. */
function sendOutlook_(to, cc, subject, html) {
  const svc = outlookService_();
  if (!svc.hasAccess()) {
    throw new Error('Outlook is not connected yet. In the script editor, run connectOutlook and sign in.');
  }
  const list = function (s) {
    return String(s || '').split(',').filter(String).map(function (a) { return { emailAddress: { address: a } }; });
  };
  const message = { subject: subject, body: { contentType: 'HTML', content: html }, toRecipients: list(to) };
  if (cc) message.ccRecipients = list(cc);
  const res = UrlFetchApp.fetch('https://graph.microsoft.com/v1.0/me/sendMail', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true,
    headers: { Authorization: 'Bearer ' + svc.getAccessToken() },
    payload: JSON.stringify({ message: message, saveToSentItems: true })
  });
  const code = res.getResponseCode();
  if (code === 202) return;
  let reason = res.getContentText().slice(0, 300);
  try { reason = JSON.parse(res.getContentText()).error.message || reason; } catch (err) { /* keep the raw text */ }
  throw new Error('Microsoft refused it (' + code + '): ' + reason);
}

// ---------------------------------------------------------------- helpers --
// The SECRET script property (SEND_SECRET, the app's name for it, also works).
// Spaces or line breaks around the value (easy to copy by mistake) are ignored.
function scriptSecret_() {
  const props = PropertiesService.getScriptProperties();
  return String(props.getProperty('SECRET') || props.getProperty('SEND_SECRET') || '').trim();
}

function checkSecret_(given) {
  const secret = scriptSecret_();
  return !!secret && String(given || '').trim() === secret;
}

// Says what didn't match without revealing either value
function secretError_(given) {
  const secret = scriptSecret_();
  const got = String(given || '').trim();
  if (!secret) return 'Wrong or missing secret: this script has no SECRET script property ' +
    '(Project Settings > Script properties, name SECRET).';
  if (!got) return 'Wrong or missing secret: the request had no secret.';
  return "Wrong or missing secret: the script's SECRET has " + secret.length + ' characters, the one received has ' +
    got.length + (secret.length === got.length ? ' (same length, so some characters differ).' : '.');
}

function logSheet_() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  let sheet = ss.getSheetByName(LOG_SHEET);
  if (!sheet) {
    sheet = ss.insertSheet(LOG_SHEET);
    sheet.appendRow(HEADERS);
    sheet.setFrozenRows(1);
    sheet.getRange(1, 1, 1, HEADERS.length).setFontWeight('bold');
  }
  return sheet;
}

function logRows_(sheet) {
  const n = sheet.getLastRow();
  if (n < 2) return [];
  return sheet.getRange(2, 1, n - 1, HEADERS.length).getValues().map(function (v) {
    return { at: v[0] instanceof Date ? v[0] : new Date(v[0]), mode: v[1], status: String(v[2]),
             id: normId_(v[3]), name: v[4], email: String(v[5] || '').toLowerCase() };
  });
}

function isLiveSent_(r) {
  return r.mode === 'live' && r.status.indexOf('Sent') === 0 && !isNaN(r.at);
}

function lastLiveSend_(sheet, who, days) {
  const since = new Date(Date.now() - days * 24 * 3600 * 1000);
  let last = null;
  logRows_(sheet).forEach(function (r) {
    const same = (who.id && r.id === who.id) || (who.email && r.email === who.email);
    if (same && isLiveSent_(r) && r.at >= since && (!last || r.at > last)) last = r.at;
  });
  return last;
}

function normId_(s) {
  return String(s || '').replace(/-/g, '').toLowerCase();
}

/** The app sends ';'-separated addresses; Gmail wants them comma-separated. */
function addresses_(s) {
  return String(s || '').split(/[;,]/).map(function (x) { return x.trim(); }).filter(String).join(',');
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
