// Your next step (2026-11-24): the planner's browser engine. Same rules as autoapply/next_steps.py; the fixtures in
// tests/fixtures/next_steps/ hold both to the same results (planner/run_fixtures.mjs, run by tests/test_planner_js.py).
// No DOM, no network. Dates are calendar days: parsed by their parts and counted in UTC, never new Date("YYYY-MM-DD")
// read as local time (that shows the day before in the US).
export const SECTIONS = ['today_pool', 'week1', 'health', 'money90', 'bills', 'before_you_go', 'ongoing'];
const GROUP_ORDER = ['before_you_go', 'week1', 'health', 'money90', 'bills', 'ongoing'];
export const PATHS = ['laid_off', 'told', 'change', 'looking'];
const AGES = ['u40', '40_54', '55_59', '60p', 'unknown'];
const SIZES = ['u20', '20_99', '100p', 'unknown'];
const HAS = ['severance', '401k', '401k_loan', 'equity', 'hsa', 'fsa', 'life', 'student_loans'];
const GATE_DAYS = 365;
const TODAY_MAX = 3;
const DAY = 86400000;

// ---- calendar days as integers (days since 1970-01-01, UTC) ----
export function parseDay(v) {
  if (v === null || v === undefined || v === '') return null;
  if (typeof v === 'object' && 'y' in v) return Date.UTC(v.y, v.m - 1, v.d) / DAY;
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(v));
  if (!m) throw new Error("A date didn't read right. Use the date picker, or leave it blank.");
  const [y, mo, d] = [+m[1], +m[2], +m[3]];
  const n = Date.UTC(y, mo - 1, d) / DAY;
  const back = new Date(n * DAY);
  if (back.getUTCFullYear() !== y || back.getUTCMonth() !== mo - 1 || back.getUTCDate() !== d) {
    throw new Error("A date didn't read right. Use the date picker, or leave it blank.");
  }
  return n;
}
export function isoDay(n) {
  const t = new Date(n * DAY);
  const p = (x) => String(x).padStart(2, '0');
  return `${t.getUTCFullYear()}-${p(t.getUTCMonth() + 1)}-${p(t.getUTCDate())}`;
}
const yearOf = (n) => new Date(n * DAY).getUTCFullYear();
const endOfMonth = (n) => { const t = new Date(n * DAY); return Date.UTC(t.getUTCFullYear(), t.getUTCMonth() + 1, 0) / DAY; };

export function validateAnswers(raw, states) {
  if (!raw || typeof raw !== 'object') throw new Error("That plan didn't read right. Try saving it again from the planner.");
  if (raw.v !== 1) throw new Error('That plan is from a newer planner. Answer the questions again here.');
  if (!PATHS.includes(raw.path)) throw new Error("That plan didn't say which situation you're in.");
  let state = typeof raw.state === 'string' && raw.state ? raw.state.toUpperCase() : null;
  if (state && states && !(state in states)) throw new Error("We don't know that state. Pick it from the list.");
  const cov = raw.coverage_end || 'unknown';
  if (!['end_of_month', 'unknown', 'none'].includes(cov)) parseDay(cov);
  const age = raw.age || 'unknown';
  const size = raw.size || 'unknown';
  if (!AGES.includes(age) || !SIZES.includes(size)) throw new Error("That plan didn't read right. Try saving it again from the planner.");
  const day = (v) => { const n = parseDay(v); return n === null ? null : isoDay(n); };
  return {
    v: 1, facts_version: String(raw.facts_version || ''), path: raw.path, last_day: day(raw.last_day), state,
    coverage_end: cov, age, size, has: (raw.has || []).filter((h) => HAS.includes(h)),
    vest_date: day(raw.vest_date), bonus_date: day(raw.bonus_date),
    noncompete: ['yes', 'no', 'unknown'].includes(raw.noncompete) ? raw.noncompete : 'unknown',
  };
}

// The code is the same as the app's: "NS1." + base64url of the answers' JSON with sorted keys, no padding.
function sortKeys(o) {
  if (Array.isArray(o)) return o.map(sortKeys);
  if (o && typeof o === 'object') return Object.keys(o).sort().reduce((a, k) => { a[k] = sortKeys(o[k]); return a; }, {});
  return o;
}
const b64url = (s) => (typeof btoa === 'function' ? btoa(unescape(encodeURIComponent(s))) : Buffer.from(s, 'utf8').toString('base64'))
  .replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64url = (s) => { const t = s.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (s.length % 4)) % 4);
  return typeof atob === 'function' ? decodeURIComponent(escape(atob(t))) : Buffer.from(t, 'base64').toString('utf8'); };
export function encodeCode(answers) {
  // Python's json.dumps(separators=(",",":")) escapes non-ASCII; answers hold only ASCII, so the two match.
  return 'NS1.' + b64url(JSON.stringify(sortKeys(answers)));
}
export function decodeCode(code, states) {
  code = (code || '').trim();
  if (!code.startsWith('NS1.')) throw new Error("That doesn't look like a plan code. It starts with NS1.");
  let data;
  try { data = JSON.parse(unb64url(code.slice(4))); } catch { throw new Error("That plan code didn't read right. Copy it again, all of it."); }
  return validateAnswers(data, states);
}

function gateOk(entry, today) {
  let checked;
  try { checked = parseDay(String(entry.checked)); } catch { return false; }
  return entry.source === 'official' && entry.confidence === 'high' && checked !== null && today - checked <= GATE_DAYS;
}
export function fieldChecked(facts, state, field) {
  const entry = (facts.states || {})[state || ''] || {};
  const val = entry[field];
  return String(((val && typeof val === 'object') ? val : entry).checked || '') || null;
}
export function stateLine(facts, state, field, today) {
  const entry = (facts.states || {})[state || ''];
  if (!entry) return [null, null];
  const url = entry.url || null;
  const val = entry[field];
  if (val && typeof val === 'object') { // a layoff fact (2026-11-24): its own text, source and gate
    return val.text && gateOk(val, today) ? [String(val.text), val.url || url] : [null, url];
  }
  if (!(field in entry) || !gateOk(entry, today)) return [null, url];
  const name = entry.name || state;
  if (field === 'waiting_week') {
    return [entry[field] ? `In ${name}, the first week of a claim is unpaid (the waiting week). Claim it anyway, or it may not count.`
      : `${name} has no unpaid waiting week.`, url];
  }
  return [null, url];
}

function anchors(a, today) {
  const L = a.last_day ? parseDay(a.last_day) : null;
  const cov = a.coverage_end;
  let C = null;
  if (cov === 'end_of_month') C = L === null ? null : endOfMonth(L);
  else if (!['unknown', 'none', null, undefined].includes(cov)) C = parseDay(cov);
  const year = yearOf(L === null ? today : L) + 1;
  return { L, C, today, vest: a.vest_date ? parseDay(a.vest_date) : null, bonus: a.bonus_date ? parseDay(a.bonus_date) : null,
    tax_day: Date.UTC(year, 3, 15) / DAY, jan31: Date.UTC(year, 0, 31) / DAY };
}

function applies(step, a) {
  if (!step.path.includes(a.path)) return false;
  const w = step.when || {};
  const cov = a.coverage_end || 'unknown';
  const covKind = cov === 'none' ? 'none' : (cov === 'unknown' ? 'unknown' : 'known');
  return [
    (w.has_all || []).every((h) => a.has.includes(h)),
    'age_in' in w ? w.age_in.includes(a.age) : true,
    'size_in' in w ? w.size_in.includes(a.size) : true,
    'coverage' in w ? (w.coverage === 'any_cover' ? covKind !== 'none' : covKind === w.coverage) : true,
    'last_day' in w ? ((a.last_day !== null) === (w.last_day === 'known')) : true,
    'noncompete_in' in w ? w.noncompete_in.includes(a.noncompete) : true,
    w.vest === 'known' ? !!a.vest_date : true,
    w.bonus === 'known' ? !!a.bonus_date : true,
  ].every(Boolean);
}

const pub = (s) => Object.fromEntries(Object.entries(s).filter(([k]) => !k.startsWith('_')));

export function plan(answers, facts, todayIn) {
  const today = parseDay(todayIn);
  const a = validateAnswers(answers, facts.states);
  const an = anchors(a, today);
  const made = []; const findOut = [];
  for (const step of facts.steps) {
    if (!applies(step, a)) continue;
    let due = null; let unknown = false;
    if (step.due) {
      const base = an[step.due.from];
      if (base === null || base === undefined) unknown = true;
      else due = base + Number(step.due.days || 0);
    }
    let note = null; let url = null; let noteChecked = null;
    if (step.state_field && a.state) {
      const fields = Array.isArray(step.state_field) ? step.state_field : [step.state_field];
      const got = fields.map((f) => stateLine(facts, a.state, f, today));
      noteChecked = fields.filter((f, i) => got[i][0]).map((f) => fieldChecked(facts, a.state, f) || '').sort().pop() || null;
      note = got.filter(([n]) => n).map(([n]) => n).join(' ') || null;
      url = (got.find(([n, u]) => n && u) || got.find(([, u]) => u) || [null, null])[1];
    }
    if (step.state_only && !note) continue; // a step that is only its state's rule
    const s = { id: step.id, title: step.title, body: String(step.body).split(/\s+/).join(' '), due: due === null ? null : isoDay(due),
      past: due !== null && due < today, source: step.source || null, state_note: note, state_url: url,
      checked: step.checked ? String(step.checked) : null, state_checked: noteChecked,
      _section: step.section, _rank: Number(step.rank ?? 99) };
    (unknown && step.section !== 'today_pool' ? findOut : made).push(s);
  }
  const byDueRank = (x, y) => ((x.due || '9999') < (y.due || '9999') ? -1 : (x.due || '9999') > (y.due || '9999') ? 1 : x._rank - y._rank);
  const past = made.filter((s) => s.past).sort(byDueRank);
  const pool = made.filter((s) => s._section === 'today_pool' && !s.past).sort((x, y) => x._rank - y._rank);
  const todayList = past.concat(pool.slice(0, Math.max(0, TODAY_MAX - past.length)));
  const taken = new Set(todayList.map((s) => s.id));
  const rest = made.filter((s) => !taken.has(s.id)).map((s) => (s._section === 'today_pool' ? { ...s, _section: 'week1' } : s));
  const dated = rest.filter((s) => s.due && !s.past).sort(byDueRank);
  const groups = [];
  for (const sec of GROUP_ORDER) {
    const steps = rest.filter((s) => s._section === sec).sort(byDueRank);
    if (steps.length) groups.push({ section: sec, steps: steps.map(pub) });
  }
  return { today: todayList.map(pub), next: dated.length ? pub(dated[0]) : null, groups, find_out: findOut.map(pub),
    facts_version: facts.version, facts_checked: facts.checked || null };
}
