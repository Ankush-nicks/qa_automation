/**
 * QA email sender: a Google Apps Script web app attached to a Google Sheet.
 * The QA app POSTs each email here. This script sends it from the Gmail account
 * that deploys it and logs every attempt in the "Sent log" tab, which is also
 * how repeat emails are caught. Setup steps: GOOGLE_SHEETS.md.
 *
 * Script property (Project Settings > Script properties):
 *   SECRET  - the same value as SEND_SECRET on the QA app's server (required)
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
  if (!checkSecret_(body.secret)) return json_({ ok: false, error: 'Wrong or missing secret.' });

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

    const options = { htmlBody: String(body.html || ''), name: String(body.sender_name || 'Central QA Team') };
    if (cc) options.cc = cc;
    try {
      GmailApp.sendEmail(to, subject, String(body.text || ''), options);
    } catch (err) {
      row[2] = 'Failed';
      row[12] = String(err && err.message || err);
      sheet.appendRow(row);
      return json_({ ok: false, error: 'Gmail couldn’t send the email: ' + row[12] });
    }
    row[2] = body.force ? 'Sent again (confirmed)' : 'Sent';
    sheet.appendRow(row);
    return json_({ ok: true, sent: true, mode: mode, sent_at: now.toISOString(),
                   quota_left: MailApp.getRemainingDailyQuota() });
  } finally {
    lock.releaseLock();
  }
}

/** GET ?action=recent&days=7&secret=... : the live emails sent in the last N days (for the app's "Emailed" tags). */
function doGet(e) {
  const p = (e && e.parameter) || {};
  if (!checkSecret_(p.secret)) return json_({ ok: false, error: 'Wrong or missing secret.' });
  if (p.action !== 'recent') return json_({ ok: true, service: 'QA email sender' });
  const days = Number(p.days) > 0 ? Number(p.days) : DEFAULT_REPEAT_DAYS;
  const since = new Date(Date.now() - days * 24 * 3600 * 1000);
  const rows = logRows_(logSheet_()).filter(function (r) { return isLiveSent_(r) && r.at >= since; });
  return json_({ ok: true, days: days, sent: rows.map(function (r) {
    return { instructor_id: r.id, instructor: r.name, email: r.email, sent_at: r.at.toISOString() };
  }) });
}

// ---------------------------------------------------------------- helpers --
function checkSecret_(given) {
  const secret = PropertiesService.getScriptProperties().getProperty('SECRET');
  return !!secret && String(given || '') === secret;
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
