// Runs the shared fixtures (tests/fixtures/next_steps/*.json) against planner.js. Exit 1 on any mismatch.
//   python scripts/export_planner.py --stdout > /tmp/facts.json && node planner/run_fixtures.mjs /tmp/facts.json
import { readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { plan, encodeCode, decodeCode } from './planner.js';

const here = dirname(fileURLToPath(import.meta.url));
const base = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const dir = join(here, '..', 'tests', 'fixtures', 'next_steps');
let failed = 0;
const fail = (name, msg) => { console.log(`FAIL ${name}: ${msg}`); failed++; };
const eq = (a, b) => JSON.stringify(a) === JSON.stringify(b);

for (const file of readdirSync(dir).filter((f) => f.endsWith('.json')).sort()) {
  const fx = JSON.parse(readFileSync(join(dir, file), 'utf8'));
  const facts = structuredClone(base);
  for (const [code, fields] of Object.entries((fx.facts_patch || {}).states || {})) Object.assign(facts.states[code], fields);
  const out = plan(fx.answers, facts, fx.today);
  const every = [...out.today, ...out.find_out, ...out.groups.flatMap((g) => g.steps)];
  const ids = (xs) => xs.map((s) => s.id);
  const ex = fx.expect;
  if ('today_ids' in ex && !eq(ids(out.today), ex.today_ids)) fail(file, `today ${ids(out.today)}`);
  if ('next_id' in ex && (out.next ? out.next.id : null) !== ex.next_id) fail(file, `next ${out.next && out.next.id}`);
  for (const [sid, due] of Object.entries(ex.due || {})) { const s = every.find((x) => x.id === sid); if (!s || s.due !== due) fail(file, `${sid} due ${s && s.due}`); }
  if ('find_out_ids' in ex && !eq(ids(out.find_out), ex.find_out_ids)) fail(file, `find_out ${ids(out.find_out)}`);
  if ('past_ids' in ex && !eq(every.filter((s) => s.past).map((s) => s.id), ex.past_ids)) fail(file, 'past');
  if ('state_note_ids' in ex && !eq(every.filter((s) => s.state_note).map((s) => s.id), ex.state_note_ids)) fail(file, 'state_note');
  for (const sid of ex.state_url_ids || []) if (!every.find((x) => x.id === sid).state_url) fail(file, `${sid} state_url`);
  for (const sid of ex.includes_ids || []) if (!ids(every).includes(sid)) fail(file, `missing ${sid}`);
  for (const sid of ex.excludes_ids || []) if (ids(every).includes(sid)) fail(file, `unexpected ${sid}`);
  if (new Set(ids(every)).size !== every.length) fail(file, 'a step shows twice');
  if (decodeCode(encodeCode(fx.answers), facts.states).path !== fx.answers.path) fail(file, 'code round trip');
}
console.log(failed ? `${failed} failed` : 'all fixtures pass');
process.exit(failed ? 1 : 0);
