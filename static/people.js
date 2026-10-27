/* Name search on both pages: type part of a name, pick an instructor or a capability manager, see their details.
   Instructor: yesterday's sessions and hours, hours per day over 14 days (over 6 h is too much), last 3 QA days,
   check-ins and 1:1s. Manager: their instructors with QA and yesterday's workload.
   Data: /api/people (the newest saved drill-down export, or the date picked on the drill-down page). */
(function () {
  const nav = document.querySelector('nav.pagenav');
  if (!nav) return;
  const HEAVY = 360; // minutes in a day above which the workload is too much (6 h)

  const css = `
.ps{position:relative;max-width:440px;margin:-6px 0 18px}
.ps input{width:100%;font:inherit;font-size:15px;padding:9px 12px 9px 34px;border:1px solid var(--rule);border-radius:6px;background:var(--sheet) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='16' height='16' fill='none' stroke='%236B7280' stroke-width='2' viewBox='0 0 24 24'%3E%3Ccircle cx='11' cy='11' r='7'/%3E%3Cpath d='m20 20-3.5-3.5'/%3E%3C/svg%3E") no-repeat 11px center;color:var(--ink)}
.ps input:focus{outline:2px solid var(--blue);outline-offset:1px;border-color:transparent}
.ps-list{position:absolute;left:0;top:calc(100% + 4px);width:max(100%,min(520px,calc(100vw - 32px)));background:var(--sheet);border:1px solid var(--rule);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.12);z-index:30;padding:4px;max-height:60vh;overflow:auto}
.ps-list[hidden]{display:none}
.ps-opt{display:block;width:100%;text-align:left;font:inherit;background:none;border:0;border-radius:5px;padding:8px 10px;cursor:pointer;color:var(--ink)}
.ps-opt b{font-weight:600}
.ps-opt span{display:block;font-size:13px;color:var(--muted);margin-top:1px}
.ps-opt[aria-selected="true"],.ps-opt:hover{background:var(--blue-tint)}
.ps-note{padding:8px 10px;font-size:13px;color:var(--muted)}
.ps-panel{border:1px solid var(--rule);border-radius:8px;background:var(--sheet);padding:18px 20px;margin:0 0 22px}
.ps-panel[hidden]{display:none}
.ps-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.ps-head h2{margin:0;font-size:20px}
.ps-sub{color:var(--muted);font-size:14px;margin-top:3px}
.ps-link{background:none;border:0;padding:0;font:inherit;color:var(--blue-ink);text-decoration:underline;text-underline-offset:3px;cursor:pointer}
.ps-close{flex:none}
.ps-sec{margin-top:18px}
.ps-sec h3{font-size:15px;margin:0 0 6px}
.ps-sec h3 small{font-weight:400;color:var(--muted);font-size:14px}
.ps-pill{display:inline-block;font-size:12px;font-weight:600;padding:1px 8px;border-radius:10px;vertical-align:2px;margin-left:6px}
.ps-pill.bad{color:var(--red);background:var(--red-tint)}
.ps-pill.warn{color:var(--amber);background:var(--amber-tint)}
.ps-pill.ok{color:var(--green);background:var(--green-tint)}
.ps-bad{color:var(--red);font-weight:600}
.ps-scroll{overflow-x:auto}
.ps table{border-collapse:collapse;width:100%;font-size:13.5px}
.ps-panel table{border-collapse:collapse;width:100%;font-size:13.5px}
.ps-panel th{text-align:left;font-weight:600;color:var(--muted);padding:6px 10px 6px 0;border-bottom:1px solid var(--rule);white-space:nowrap}
.ps-panel td{padding:7px 10px 7px 0;border-bottom:1px solid var(--rule-soft);vertical-align:top}
.ps-panel .num{text-align:right;font-variant-numeric:tabular-nums}
.ps-bars{display:flex;gap:4px;align-items:flex-end;height:96px;position:relative;margin-top:8px;padding-top:4px;max-width:640px}
.ps-bars .six{position:absolute;left:0;right:0;border-top:1px dashed var(--red);opacity:.6}
.ps-bars .six span{position:absolute;right:0;top:-16px;font-size:11px;color:var(--red)}
.ps-bar{flex:1;min-width:14px;display:flex;flex-direction:column;justify-content:flex-end;align-items:stretch;height:100%;background:none;border:0;padding:0;cursor:pointer}
.ps-bar i{display:block;background:var(--blue);border-radius:3px 3px 0 0;min-height:2px}
.ps-bar.zero i{background:var(--rule)}
.ps-bar.heavy i{background:var(--red)}
.ps-bar[aria-pressed="true"] i{outline:2px solid var(--ink);outline-offset:1px}
.ps-days{display:flex;gap:4px;max-width:640px;font-size:11px;color:var(--muted);margin-top:3px}
.ps-days span{flex:1;min-width:14px;text-align:center;white-space:nowrap;overflow:hidden}
.ps-facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px 18px;margin-top:6px;font-size:14px}
.ps-facts div small{display:block;color:var(--muted);font-size:12.5px}
.ps-facts div b{font-size:17px}
@media (max-width:700px){.ps{max-width:none}.ps-panel{padding:14px}}`;
  document.head.insertAdjacentHTML('beforeend', `<style>${css}</style>`);

  const box = document.createElement('div');
  box.className = 'ps';
  box.innerHTML = `<input id="psInput" type="search" autocomplete="off" spellcheck="false" placeholder="Search an instructor or manager by name"
    role="combobox" aria-expanded="false" aria-controls="psList" aria-autocomplete="list" aria-label="Search an instructor or manager by name">
    <div class="ps-list" id="psList" role="listbox" hidden></div>`;
  // the tabs, the search and its results sit above the page's own header, full width, on both pages
  const hdr = nav.closest('header');
  if (hdr) hdr.before(nav);
  nav.insertAdjacentElement('afterend', box);
  const panel = document.createElement('section');
  panel.className = 'ps-panel'; panel.hidden = true; panel.setAttribute('aria-live', 'polite');
  box.insertAdjacentElement('afterend', panel);
  const input = box.querySelector('input'), list = box.querySelector('.ps-list');

  /* ---------- helpers ---------- */
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const dOf = s => new Date(s.slice(0, 10) + 'T00:00:00');
  const iso = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
  const addDays = (s, n) => { const d = dOf(s); d.setDate(d.getDate() + n); return iso(d); };
  const fD = s => { const d = dOf(s); return `${d.getDate()} ${MON[d.getMonth()]}`; };
  const fWD = s => `${WD[dOf(s).getDay()]} ${fD(s)}`;
  const fH = m => { m = Math.round(m || 0); if (!m) return '0 h'; const h = Math.floor(m / 60), r = m % 60; return h ? `${h} h${r ? ` ${r} min` : ''}` : `${r} min`; };
  const title = s => (s || '').charAt(0) + (s || '').slice(1).toLowerCase();
  const norm = s => (s || '').toLowerCase().normalize('NFKD').replace(/[^a-z0-9 ]+/g, ' ').replace(/\s+/g, ' ').trim();
  function grams(s) { const m = new Map(), t = ` ${s} `; for (let i = 0; i < t.length - 1; i++) { const g = t.slice(i, i + 2); m.set(g, (m.get(g) || 0) + 1); } return m; }
  function dice(a, b) {
    if (!a || !b) return 0;
    const A = grams(a), B = grams(b); let x = 0, na = 0, nb = 0;
    A.forEach(v => { na += v; }); B.forEach(v => { nb += v; });
    A.forEach((v, g) => { if (B.has(g)) x += Math.min(v, B.get(g)); });
    return 2 * x / (na + nb);
  }
  // 1000 exact … 700 contains; below that, how alike the spelling is (0-100)
  function score(q, p) {
    const n = p.key, toks = p.toks;
    if (n === q) return 1000;
    if (n.startsWith(q)) return 900;
    if (toks.some(t => t.startsWith(q))) return 850;
    const qt = q.split(' ');
    if (qt.length > 1 && qt.every(x => toks.some(t => t.startsWith(x)))) return 800;
    if (n.includes(q)) return 700;
    return 100 * Math.max(dice(q, n), ...toks.map(t => dice(q, t)), ...toks.map(t => dice(q, t.slice(0, q.length + 1))));
  }

  /* ---------- data ---------- */
  let P = null, loading = null, people = [], loadedFor = null;
  const runPicked = () => { const s = document.getElementById('runDate'); return s && s.value ? s.value : ''; };
  function load() {
    const want = runPicked();
    if (P && loadedFor === want) return Promise.resolve(P);
    if (loading && loadedFor === want) return loading;
    loadedFor = want;
    loading = fetch('/api/people' + (want ? `?date=${encodeURIComponent(want)}` : ''))
      .then(r => r.json().then(j => { if (!r.ok || j.error) throw new Error(j.error || `Error ${r.status}`); return j; }))
      .then(j => { P = prep(j); return P; })
      .catch(e => { loading = null; loadedFor = null; throw e; });
    return loading;
  }
  function prep(j) {
    j.YDAY = addDays(j.run_date, -1);
    j.days = []; for (let s = j.from; s <= j.run_date; s = addDays(s, 1)) j.days.push(s);
    const mon = s => { const d = dOf(s), k = (d.getDay() + 6) % 7; d.setDate(d.getDate() - k); return iso(d); };
    j.LASTWK = addDays(mon(j.run_date), -7);
    const C = j.sess_cols || [], ix = Object.fromEntries(C.map((c, k) => [c, k]));
    j.ix = ix;
    j.byId = {};
    j.instructors.forEach(i => { j.byId[i.instructor_user_id] = i; });
    const capKey = new Set(j.caps.map(c => c.toLowerCase()));
    j.capsOf = i => (i.managers || '').split(', ').filter(m => m && capKey.has(m.toLowerCase()));
    j.mgr = {};
    j.caps.forEach(name => {
      const rows = j.managers_weekly.filter(r => (r.manager || '').toLowerCase() === name.toLowerCase());
      const ins = j.instructors.filter(i => i.status === 'ACTIVE' && j.capsOf(i).some(m => m.toLowerCase() === name.toLowerCase()));
      const lw = rows.find(r => r.week === j.LASTWK);
      j.mgr[name] = { name, cat: (rows.find(r => r.category) || {}).category, active: ins.length, ins, lastWeek: lw };
    });
    people = [];
    j.instructors.forEach(i => {
      if (!i.instructor_name) return;
      if (i.status !== 'ACTIVE' && !i.sessions_14d && i.qa_last3_avg == null) return;
      const key = norm(i.instructor_name);
      people.push({ kind: 'i', id: i.instructor_user_id, name: i.instructor_name, key, toks: key.split(' '),
        sub: ['Instructor', i.category, i.home_institute].filter(Boolean).join(' · ') + (i.status && i.status !== 'ACTIVE' ? ' · inactive' : ''),
        flag: i.low_qa });
    });
    j.caps.forEach(name => {
      const m = j.mgr[name], key = norm(name);
      people.push({ kind: 'm', id: name, name, key, toks: key.split(' '),
        sub: ['Capability manager', m.cat, `${m.active} active instructor${m.active === 1 ? '' : 's'}`].filter(Boolean).join(' · ') });
    });
    return j;
  }
  // per instructor: {day: {n, mins, rows}}
  function daysOf(id) {
    const out = {}, L = (P.sessions || {})[id] || [], ix = P.ix;
    L.forEach(r => {
      const d = r[ix.day]; const o = out[d] || (out[d] = { n: 0, mins: 0, rows: [] });
      o.n++; o.mins += r[ix.minutes] || 0; o.rows.push(r);
    });
    return out;
  }

  /* ---------- suggestions ---------- */
  let opts = [], at = -1;
  function showList(html) { list.innerHTML = html; list.hidden = false; input.setAttribute('aria-expanded', 'true'); }
  function hideList() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); at = -1; }
  function optHtml(p, k) {
    return `<button type="button" class="ps-opt" role="option" id="psOpt${k}" data-k="${k}" aria-selected="false"><b>${esc(p.name)}</b>${p.flag ? '<span class="ps-pill bad" style="display:inline-block;margin-left:6px">below 5</span>' : ''}<span>${esc(p.sub)}</span></button>`;
  }
  async function suggest() {
    const q = norm(input.value);
    if (!q) { hideList(); return; }
    if (!P) showList('<div class="ps-note">Loading names…</div>');
    try { await load(); } catch (e) { showList(`<div class="ps-note">${esc(e.message)}</div>`); return; }
    if (norm(input.value) !== q) return;
    const scored = people.map(p => ({ p, s: score(q, p) })).sort((a, b) => b.s - a.s || a.p.name.localeCompare(b.p.name));
    const strong = scored.filter(x => x.s >= 700);
    if (strong.length) {
      opts = strong.slice(0, 8).map(x => x.p);
      showList(opts.map(optHtml).join('') + (strong.length > 8 ? `<div class="ps-note">${strong.length - 8} more match. Keep typing to narrow it down.</div>` : ''));
    } else {
      opts = scored.filter(x => x.s >= 35).slice(0, 6).map(x => x.p);
      showList(opts.length ? `<div class="ps-note">No name matches “${esc(input.value.trim())}”. Did you mean:</div>` + opts.map(optHtml).join('')
        : `<div class="ps-note">No instructor or manager matches “${esc(input.value.trim())}”. Check the spelling or try part of the name.</div>`);
    }
    at = -1;
  }
  function move(d) {
    const B = [...list.querySelectorAll('.ps-opt')]; if (!B.length) return;
    at = (at + d + B.length) % B.length;
    B.forEach((b, k) => b.setAttribute('aria-selected', String(k === at)));
    B[at].scrollIntoView({ block: 'nearest' }); input.setAttribute('aria-activedescendant', B[at].id);
  }
  function pick(k) {
    const p = opts[k]; if (!p) return;
    input.value = p.name; hideList();
    p.kind === 'i' ? openInstructor(p.id) : openManager(p.id);
  }
  input.addEventListener('input', suggest);
  input.addEventListener('focus', () => { load().catch(() => {}); if (input.value.trim()) suggest(); });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { e.preventDefault(); if (list.hidden) suggest(); else move(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); move(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); pick(at >= 0 ? at : 0); }
    else if (e.key === 'Escape') { hideList(); }
  });
  list.addEventListener('mousedown', e => e.preventDefault());
  list.addEventListener('click', e => { const b = e.target.closest('.ps-opt'); if (b) pick(+b.dataset.k); });
  document.addEventListener('click', e => { if (!box.contains(e.target)) hideList(); });
  const rd = document.getElementById('runDate');
  if (rd) rd.addEventListener('change', () => { P = null; if (!panel.hidden) closePanel(); });

  /* ---------- details ---------- */
  let back = null;
  function closePanel() { panel.hidden = true; panel.innerHTML = ''; back = null; }
  function head(name, pills, sub) {
    return `<div class="ps-head"><div>${back ? `<button type="button" class="ps-link" data-ps-back="1">← ${esc(back.name)}</button>` : ''}
      <h2>${esc(name)}${pills}</h2><div class="ps-sub">${sub}</div></div>
      <button type="button" class="ps-link ps-close" data-ps-close="1">Close</button></div>`;
  }
  const qaDays = i => (i.qa_last3_days || '').split(' | ').filter(Boolean).map(x => { const [d, a] = x.split(' '); return { d, a: +a }; });
  const qaCell = i => { const D = qaDays(i); return D.length ? D.map(x => `${fD(x.d)} <span class="${x.a < 5 ? 'ps-bad' : ''}">${x.a.toFixed(2)}</span>`).join(' · ') : '<span class="ps-sub">no QA in 14 days</span>'; };

  function openInstructor(id, day) {
    const i = P.byId[id]; if (!i) return;
    const D = daysOf(id), has = !!P.sessions, pick = day || P.YDAY, sel = D[pick] || { n: 0, mins: 0, rows: [] };
    const caps = P.capsOf(i);
    const sub = ['Instructor', i.category, i.home_institute].filter(Boolean).map(esc).join(' · ')
      + (i.status && i.status !== 'ACTIVE' ? ` · <b>${esc(title(i.status))}</b>` : '')
      + `<br>Capability manager: ${caps.length ? caps.map(m => `<button type="button" class="ps-link" data-ps-mgr="${esc(m)}">${esc(m)}</button>`).join(', ') : 'none on the list of 14'}`
      + (i.instructor_mail ? ` · ${esc(i.instructor_mail)}` : '');
    const tot = P.days.reduce((a, d) => a + (D[d] ? D[d].mins : 0), 0), totN = P.days.reduce((a, d) => a + (D[d] ? D[d].n : 0), 0);
    const heavy = P.days.filter(d => D[d] && D[d].mins > HEAVY);
    const busiest = P.days.reduce((b, d) => (D[d] && D[d].mins > (b ? D[b].mins : 0) ? d : b), null);
    const max = Math.max(HEAVY * 1.25, ...P.days.map(d => (D[d] ? D[d].mins : 0)));
    const ix = P.ix;
    const dayLabel = pick === P.YDAY ? `Yesterday, ${fWD(pick)}` : pick === P.run_date ? `Today, ${fWD(pick)}` : fWD(pick);

    const workload = has ? `
      <div class="ps-sec"><h3>${dayLabel} <small>· ${sel.n} session${sel.n === 1 ? '' : 's'} · ${fH(sel.mins)}</small>${sel.mins > HEAVY ? '<span class="ps-pill bad">over 6 h</span>' : ''}</h3>
        ${sel.rows.length ? `<div class="ps-scroll"><table><thead><tr><th>Time</th><th>Session</th><th>University · section</th><th>Status</th><th class="num">Mins</th><th class="num">QA</th></tr></thead><tbody>${sel.rows.map(r => `<tr><td style="white-space:nowrap">${esc(r[ix.start_time])}–${esc(r[ix.end_time])}</td><td>${esc(r[ix.session_title] || '—')} <span class="ps-sub" style="display:inline">${esc(title(r[ix.session_type]))}</span></td><td>${esc(r[ix.institute_name] || '—')}${r[ix.sections] ? ` · ${esc(r[ix.sections])}` : ''}</td><td>${esc(title((r[ix.session_status] || '').replace(/_/g, ' ')))}</td><td class="num">${r[ix.minutes] ?? '—'}</td><td class="num">${r[ix.qa_rating] == null ? '—' : `<span class="${r[ix.qa_rating] < 5 ? 'ps-bad' : ''}">${(+r[ix.qa_rating]).toFixed(2)}</span>`}</td></tr>`).join('')}</tbody></table></div>`
          : `<p class="ps-sub">No sessions on ${fWD(pick)}.</p>`}
      </div>
      <div class="ps-sec"><h3>Last 14 days <small>· ${totN} sessions · ${fH(tot)}${busiest ? ` · busiest ${fD(busiest)}, ${fH(D[busiest].mins)}` : ''}</small>${heavy.length ? `<span class="ps-pill bad">${heavy.length} day${heavy.length === 1 ? '' : 's'} over 6 h</span>` : ''}</h3>
        <div class="ps-bars" role="group" aria-label="Hours per day; pick a day to see its sessions">
          <div class="six" style="bottom:${HEAVY / max * 100}%"><span>6 h</span></div>
          ${P.days.map(d => { const o = D[d] || { n: 0, mins: 0 }; return `<button type="button" class="ps-bar${!o.mins ? ' zero' : o.mins > HEAVY ? ' heavy' : ''}" data-ps-day="${d}" aria-pressed="${d === pick}" title="${fWD(d)}: ${o.n} session${o.n === 1 ? '' : 's'}, ${fH(o.mins)}" aria-label="${fWD(d)}: ${o.n} sessions, ${fH(o.mins)}"><i style="height:${Math.max(2, o.mins / max * 100)}%"></i></button>`; }).join('')}
        </div>
        <div class="ps-days">${P.days.map(d => `<span>${dOf(d).getDate()}</span>`).join('')}</div>
        <p class="ps-sub" style="font-size:12.5px">Scheduled start-to-end time of each session; a class merged across sections counts once. Pick a day to see its sessions. Red = over 6 hours.</p>
      </div>`
      : `<div class="ps-sec"><h3>Workload <small>· last 14 days</small></h3><p>${i.sessions_14d || 0} sessions · ${fH(i.minutes_14d)}</p>
         <p class="ps-sub">Yesterday's sessions and hours per day need the latest <code>niat_all_in_one.sql</code> (it adds the 14_sessions part). Upload its output to see them.</p></div>`;

    const qd = qaDays(i);
    const qa = `<div class="ps-sec"><h3>QA, last 3 QA days</h3>
      ${qd.length ? `<p>${qaCell(i)} <span class="ps-sub" style="display:inline">· average ${i.qa_last3_avg ?? '—'} from ${i.qa_last3_n || 0} score${i.qa_last3_n === 1 ? '' : 's'}</span></p>
        <p class="ps-sub">${i.low_qa ? 'Each of these days is below 5, so they are flagged on both pages.' : 'Not flagged: at least one of these days is 5 or more' + (qd.length < 3 ? ', or there are fewer than 3 QA days' : '') + '.'}</p>`
        : '<p class="ps-sub">No QA score in the last 14 days.</p>'}
    </div>`;
    const other = `<div class="ps-sec"><h3>Other</h3><div class="ps-facts">
      <div><small>Taught at, 14 days</small>${esc(i.taught_at || 'no session')}</div>
      <div><small>Zero QA, 14 days</small><b class="${i.zero_qa_14d ? 'ps-bad' : ''}">${i.zero_qa_14d || 0}</b></div>
      <div><small>Checked in</small><b>${i.checked_in_days ?? '—'}</b> of ${i.checkin_days ?? '—'} days${i.late_days ? ` · late ${i.late_days}` : ''}</div>
      <div><small>Last completed 1:1</small><b>${i.last_completed_connect ? fD(i.last_completed_connect) : '—'}</b>${i.connect_statuses_this_week ? `<br><span class="ps-sub">this week: ${esc(i.connect_statuses_this_week)}</span>` : ''}</div>
    </div></div>`;

    panel.innerHTML = head(i.instructor_name, i.low_qa ? '<span class="ps-pill bad">below 5</span>' : '', sub) + workload + qa + other
      + `<p class="ps-sub" style="margin-top:16px;font-size:12.5px">From the export of ${fWD(P.run_date)}.</p>`;
    panel.dataset.id = id; panel.hidden = false;
    if (!day) panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function openManager(name) {
    const m = P.mgr[name]; if (!m) return;
    back = null;
    const lw = m.lastWeek, conn = lw ? lw.instructors_connected || 0 : 0;
    const rows = m.ins.map(i => {
      const D = daysOf(i.instructor_user_id), y = D[P.YDAY] || { n: 0, mins: 0 };
      const tot = P.days.reduce((a, d) => a + (D[d] ? D[d].mins : 0), 0);
      const heavy = P.days.filter(d => D[d] && D[d].mins > HEAVY).length;
      return { i, y, tot, heavy };
    }).sort((a, b) => (b.i.low_qa - a.i.low_qa) || ((a.i.qa_last3_avg ?? 99) - (b.i.qa_last3_avg ?? 99)) || (a.i.instructor_name || '').localeCompare(b.i.instructor_name || ''));
    const low = rows.filter(r => r.i.low_qa).length, has = !!P.sessions;
    const sub = ['Capability manager', m.cat, `${m.active} active instructor${m.active === 1 ? '' : 's'}`].filter(Boolean).map(esc).join(' · ')
      + `<br>1:1s in the week of ${fD(P.LASTWK)}: ${conn} of ${m.active} instructors connected${m.active ? ` (${Math.round(conn / m.active * 1000) / 10}%)` : ''}`;
    panel.innerHTML = head(name, low ? `<span class="ps-pill bad">${low} below 5</span>` : '', sub)
      + `<div class="ps-sec"><h3>Instructors <small>· below 5 first, then lowest QA</small></h3>
        ${rows.length ? `<div class="ps-scroll"><table><thead><tr><th>Instructor</th><th>Last 3 QA days</th><th class="num">Yesterday</th><th class="num">14 days</th><th>Last 1:1</th></tr></thead><tbody>${rows.map(r => `<tr>
          <td><button type="button" class="ps-link" data-ps-ins="${esc(r.i.instructor_user_id)}">${esc(r.i.instructor_name || 'Unnamed')}</button>${r.i.low_qa ? '<span class="ps-pill bad">below 5</span>' : ''}</td>
          <td style="white-space:nowrap">${qaCell(r.i)}</td>
          <td class="num" style="white-space:nowrap">${has ? `${r.y.n} · ${fH(r.y.mins)}${r.y.mins > HEAVY ? ' <span class="ps-pill bad">over 6 h</span>' : ''}` : '—'}</td>
          <td class="num" style="white-space:nowrap">${has ? fH(r.tot) + (r.heavy ? ` <span class="ps-pill warn">${r.heavy} heavy</span>` : '') : fH(r.i.minutes_14d)}</td>
          <td style="white-space:nowrap">${r.i.last_completed_connect ? fD(r.i.last_completed_connect) : '—'}</td></tr>`).join('')}</tbody></table></div>
          <p class="ps-sub" style="font-size:12.5px">Yesterday = ${fWD(P.YDAY)}: sessions · scheduled hours. Heavy = days over 6 hours in the last 14. Click a name for their details.</p>`
          : '<p class="ps-sub">No active instructors mapped to this manager.</p>'}</div>`
      + `<p class="ps-sub" style="margin-top:16px;font-size:12.5px">From the export of ${fWD(P.run_date)}.</p>`;
    panel.dataset.mgr = name; panel.hidden = false;
    panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  panel.addEventListener('click', e => {
    const t = e.target.closest('button'); if (!t) return;
    if (t.dataset.psClose) { closePanel(); input.value = ''; input.focus(); }
    else if (t.dataset.psBack) { const b = back; back = null; openManager(b.name); }
    else if (t.dataset.psMgr) { input.value = t.dataset.psMgr; openManager(t.dataset.psMgr); }
    else if (t.dataset.psIns) { back = { name: panel.dataset.mgr }; input.value = P.byId[t.dataset.psIns].instructor_name; openInstructor(t.dataset.psIns); }
    else if (t.dataset.psDay) openInstructor(panel.dataset.id, t.dataset.psDay);
  });
})();
