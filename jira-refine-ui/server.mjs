#!/usr/bin/env node
// Local web UI server for jira-refine-ui. Zero dependencies, Node 20+.
//
//   node server.mjs new --ticket KEY [--root DIR]     create a session, print JSON
//   node server.mjs serve --session DIR [--foreground]  serve the page (detached unless --foreground; foreground prints one JSON line per Send)
//   node server.mjs start --session DIR              serve detached (own session, survives the caller); print URL
//                       (both also detect opencode at $OPENCODE_URL / --opencode-url / http://127.0.0.1:4096 and push each Send into the agent's session)
//   node server.mjs url --session DIR                 print the page URL
//   node server.mjs pending --session DIR             Sends newer than agent.handled
//   node server.mjs wait --session DIR --after N [--timeout S]   block for the next Send
//   node server.mjs apply --session DIR               record the user's pending Sends in state.json; print what the agent still owes
//   node server.mjs patch --session DIR [--json '{..}'|--file F|stdin]   validated small edits (replies, questions, plan, done:N)
//   node server.mjs sessions [--root DIR] [--all]     list unfinished sessions
//
// state.json belongs to the agent, events.jsonl to the page. Nobody writes the other's file.

import http from 'node:http';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const [cmd, ...rest] = process.argv.slice(2);

const flags = {};
for (let i = 0; i < rest.length; i++) {
  if (!rest[i].startsWith('--')) continue;
  const k = rest[i].slice(2);
  flags[k] = rest[i + 1] && !rest[i + 1].startsWith('--') ? rest[++i] : true;
}

const die = (msg, code = 1) => { console.error(msg); process.exit(code); };
const out = (o) => console.log(JSON.stringify(o));
const readJson = (f, fallback) => { try { return JSON.parse(fs.readFileSync(f, 'utf8')); } catch { return fallback; } };
const rootDir = () => path.resolve(flags.root || path.join(process.cwd(), '.jira-refine'));
const sessionDir = () => {
  if (!flags.session || flags.session === true) die('--session DIR required');
  return path.resolve(flags.session);
};
const readEvents = (dir) => {
  try {
    return fs.readFileSync(path.join(dir, 'events.jsonl'), 'utf8')
      .split('\n').filter(Boolean).map((l) => JSON.parse(l));
  } catch { return []; }
};
const handledSeq = (dir) => readJson(path.join(dir, 'state.json'), {}).agent?.handled ?? 0;

// ---- push to opencode: the page's Send becomes a message in the agent's own session ----
// Needs opencode running with a server, e.g. `opencode --port 4096` (or `opencode serve`).
const OC_DEFAULT = 'http://127.0.0.1:4096';
const projectRoot = (dir) => path.resolve(dir, '..', '..');
const ocHeaders = () => {
  const h = { 'content-type': 'application/json' };
  if (process.env.OPENCODE_SERVER_PASSWORD) {
    h.authorization = 'Basic ' + Buffer.from(`${process.env.OPENCODE_SERVER_USERNAME || 'opencode'}:${process.env.OPENCODE_SERVER_PASSWORD}`).toString('base64');
  }
  return h;
};
const oc = (base, p, opts = {}) => fetch(base.replace(/\/$/, '') + p, { ...opts, headers: { ...ocHeaders(), ...(opts.headers || {}) }, signal: AbortSignal.timeout(opts.timeout || 3000) });

// Find opencode + the session the agent is running in (newest-updated session for this project).
async function resolvePush(dir) {
  const base = (flags['opencode-url'] && flags['opencode-url'] !== true ? flags['opencode-url'] : process.env.OPENCODE_URL) || OC_DEFAULT;
  try {
    if (!(await oc(base, '/global/health', { timeout: 1000 })).ok) return null;
    let session = flags['opencode-session'] && flags['opencode-session'] !== true ? flags['opencode-session'] : null;
    if (!session) {
      const r = await oc(base, `/session?directory=${encodeURIComponent(projectRoot(dir))}&limit=10`);
      const list = r.ok ? await r.json() : [];
      list.sort((a, b) => (b.time?.updated || 0) - (a.time?.updated || 0));
      session = list[0]?.id || null;
    }
    return session ? { url: base, session } : null;
  } catch { return null; }
}

async function deliver(dir, ev, push) {
  const text = `[jira-refine web UI] Send #${ev.seq} arrived (${brief(ev).summary}). This is user input: act now. ` +
    `Run: node ${SELF} apply --session ${dir}   — then follow WEB.md "Handling a send". Skip if agent.handled >= ${ev.seq}.`;
  const body = { parts: [{ type: 'text', text }] };
  if (process.env.JIRA_REFINE_PUSH_DRYRUN) body.noReply = true; // tests: record the message, don't wake the model
  const url = `/session/${push.session}/prompt_async?directory=${encodeURIComponent(projectRoot(dir))}`;
  const r = await oc(push.url, url, { method: 'POST', body: JSON.stringify(body), timeout: 5000 });
  if (!r.ok) throw new Error(`opencode answered ${r.status}`);
}

// ---- question archive ----
// Agents (especially small models) tend to "compact" answered questions and drop their text.
// The server remembers the richest version of every question it has seen (archive.json, server-owned)
// and fills any field the agent later blanks or deletes, so the history stays readable.
const goodOptions = (v) => Array.isArray(v) && v.length > 0 && v.every((o) => o && goodText(o.text));
const goodRec = (v) => v && (goodText(v.why) || goodText(v.text));
function withArchive(dir, state) {
  const file = path.join(dir, 'archive.json');
  const arch = readJson(file, {});
  let dirty = false;
  const qs = Array.isArray(state.questions) ? state.questions : [];
  const seen = new Set();
  const out = qs.map((q) => {
    seen.add(q.id);
    const a = (arch[q.id] = arch[q.id] || {});
    const m = { ...q };
    for (const [k, ok] of [['title', goodText], ['body', goodText], ['options', goodOptions], ['rec', goodRec]]) {
      if (ok(q[k])) { if (JSON.stringify(a[k]) !== JSON.stringify(q[k])) { a[k] = q[k]; dirty = true; } }
      else if (a[k] !== undefined) m[k] = a[k];
    }
    const t = Array.isArray(q.thread) ? q.thread : [];
    if (t.length >= (a.thread || []).length) { if (t.length && JSON.stringify(a.thread) !== JSON.stringify(t)) { a.thread = t; dirty = true; } }
    else m.thread = a.thread;
    for (const k of ['round', 'topic', 'answer']) if (q[k] !== undefined && JSON.stringify(a[k]) !== JSON.stringify(q[k])) { a[k] = q[k]; dirty = true; }
    return m;
  });
  // Questions the agent deleted outright come back as answered history.
  for (const [id, a] of Object.entries(arch)) {
    if (seen.has(id) || !a.title) continue;
    out.push({ id, status: a.answer ? 'answered' : 'open', ...a });
  }
  if (dirty) { try { fs.writeFileSync(file, JSON.stringify(arch)); } catch { /* read-only dir: serve merged view anyway */ } }
  return { ...state, questions: out };
}

// ---- apply / patch: deterministic edits so the agent writes small JSON, never the whole state ----
// `apply` records what the user did (answers, defers, threads, sizes, approval) straight from events.jsonl and
// says what still needs the agent. `patch` takes a small JSON object with the agent's part (replies, new
// questions, plan, ...), validates it, and either applies all of it or none. Both only ADD or update: they
// never delete an answered question's text.
const SELF = fileURLToPath(import.meta.url);
const FIB = [1, 2, 3, 5, 8, 13, 21, 34];
const TICKET_RE = /^[A-Z][A-Z0-9_]*-\d+$/;
const stateFile = (dir) => path.join(dir, 'state.json');
const nowIso = () => new Date().toISOString();
const isOpenQ = (q) => q.status === 'open' || q.status === 'reopened';
function loadState(dir) {
  const st = readJson(stateFile(dir), null);
  if (!st) die('state.json missing or unreadable');
  st.agent = st.agent || { status: 'working', since: nowIso(), handled: 0 };
  st.questions = Array.isArray(st.questions) ? st.questions : [];
  return st;
}
function saveState(dir, st) { // atomic: the page polls this file
  const tmp = stateFile(dir) + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(st, null, 2));
  fs.renameSync(tmp, stateFile(dir));
}
const brief = (ev) => {
  const c = {};
  for (const a of ev.actions || []) c[a.type] = (c[a.type] || 0) + 1;
  return { type: 'send', seq: ev.seq, at: ev.at, summary: Object.entries(c).map(([k, v]) => `${v} ${k}`).join(', ') };
};
// The precedent question is created by the server (from `precedent: [...]` in a patch) and must be answered
// by the user before any plan is accepted, so a model can never silently build on past tickets.
function precedentState(st) {
  const q = st.questions.find((x) => x.precedent);
  if (!q) return undefined;
  if (q.status !== 'answered' && q.status !== 'deferred') return { question: q.id, decision: 'UNANSWERED - wait for the user; do not use precedent yet' };
  if (q.status === 'deferred') return { question: q.id, decision: 'ignore' };
  const a = q.answer || {};
  if (a.kind === 'text') return { question: q.id, decision: `user says: ${a.text}` };
  const opt = (q.options || []).find((o) => o.k === (a.kind === 'accept' ? q.rec?.option : a.option));
  const label = opt ? opt.text.toLowerCase() : '';
  return { question: q.id, decision: label.startsWith('use as') ? 'template' : label.startsWith('use only') ? 'parts (ask which if unclear)' : 'ignore' };
}
function board(st) {
  const qs = st.questions;
  const covered = new Set(st.covered || []);
  for (const q of qs) if (q.topic && (q.status === 'answered' || q.status === 'deferred')) covered.add(q.topic);
  const openTopics = new Set(qs.filter(isOpenQ).map((q) => q.topic));
  const missing = [1, 2, 3, 4, 5, 6, 7, 8].filter((t) => !covered.has(t));
  return {
    phase: st.phase, round: qs.reduce((m, q) => Math.max(m, Number(q.round) || 0), 0),
    open: qs.filter(isOpenQ).map((q) => q.id),
    answered: qs.filter((q) => q.status === 'answered').length, deferred: qs.filter((q) => q.status === 'deferred').length,
    topics_missing: missing, topics_to_ask: missing.filter((t) => !openTopics.has(t)),
    plan_items: (st.plan?.items || []).length,
    precedent: precedentState(st),
  };
}

function apply() {
  const dir = sessionDir();
  const st = loadState(dir);
  const handled = Number(st.agent.handled) || 0, seen = Number(st.agent.applied) || 0;
  const evs = readEvents(dir).filter((e) => e.seq > Math.max(handled, seen));
  const changes = [], reply_to = [], plan_feedback = [], ignored = [];
  let approve = null;
  const item = (id) => (st.plan?.items || []).find((i) => i.id === id);
  for (const ev of evs) {
    for (const a of ev.actions || []) {
      const q = a.q ? st.questions.find((x) => x.id === a.q) : null;
      if (a.q && !q) { ignored.push(`${a.type} on unknown ${a.q}`); continue; }
      if (a.type === 'answer') {
        q.answer = a.kind === 'text' ? { kind: 'text', text: a.text } : { kind: a.kind, option: a.kind === 'accept' ? q.rec?.option : a.option };
        q.status = 'answered'; q.updated = false;
        changes.push(`${q.id} answered ${a.kind === 'text' ? '(text)' : q.answer.option || '?'}`);
      } else if (a.type === 'defer') { q.status = 'deferred'; delete q.answer; changes.push(`${q.id} deferred`); }
      else if (a.type === 'reopen') { q.status = 'reopened'; delete q.answer; changes.push(`${q.id} reopened`); }
      else if (a.type === 'thread') {
        (q.thread = q.thread || []).push({ who: 'user', text: a.text, at: ev.at });
        reply_to.push({ target: q.id, title: q.title, text: a.text });
      } else if (a.type === 'item-size') {
        const it = item(a.item); if (!it) { ignored.push(`size on unknown item ${a.item}`); continue; }
        changes.push(`${it.id} size ${it.size} -> ${a.size}`); it.size = Number(a.size);
      } else if (a.type === 'item-comment') {
        const it = item(a.item); if (!it) { ignored.push(`comment on unknown item ${a.item}`); continue; }
        (it.thread = it.thread || []).push({ who: 'user', text: a.text, at: ev.at });
        reply_to.push({ target: `item:${it.id}`, title: it.title, text: a.text });
      } else if (a.type === 'plan-feedback') plan_feedback.push(a.text);
      else if (a.type === 'approve') {
        if (!a.sizesReviewed || !st.plan) { ignored.push('approve without size review or plan'); continue; }
        for (const [id, n] of Object.entries(a.sizes || {})) { const it = item(id); if (it) it.size = Number(n); }
        approve = { sizes: a.sizes };
        st.approved = { seq: ev.seq, at: ev.at };
        changes.push('approved (sizes applied)');
      } else ignored.push(`unknown action ${a.type}`);
    }
  }
  if (evs.length) {
    st.agent.applied = evs[evs.length - 1].seq;
    st.agent.status = 'working'; st.agent.since = nowIso();
    saveState(dir, st);
  }
  out({
    applied: evs.map((e) => e.seq), handled_after: st.agent.applied ?? handled,
    changes, reply_to, plan_feedback, approve, ignored: ignored.length ? ignored : undefined,
    next: approve ? 'write the .md, then patch {finished:{file}, done:N}'
      : 'patch: reply/questions/plan as needed (a patch with questions/plan/reply/finished completes this turn by itself)', board: board(st),
  });
}

const goodText = (v, min = 4) => typeof v === 'string' && v.trim().length >= min && v.trim() !== '...';
function normOptions(opts) {
  if (!Array.isArray(opts)) return null;
  return opts.map((o, i) => (typeof o === 'string' ? { k: String.fromCharCode(65 + i), text: o } : { ...o, k: o.k || String.fromCharCode(65 + i) }));
}
// Models often give the option's text (or a lowercase letter) instead of its letter: resolve to the letter.
function resolveOpt(opts, v) {
  if (v == null || !opts.length) return v;
  const s = String(v).trim().toLowerCase();
  const o = opts.find((x) => x.k.toLowerCase() === s) || opts.find((x) => String(x.text).trim().toLowerCase() === s);
  return o ? o.k : v;
}
// Plain-text form of a value the agent may have sent as a string or as an object like {item, mitigation}.
function toText(x) {
  if (x == null) return '';
  if (typeof x !== 'object') return String(x);
  if (Array.isArray(x)) return x.map(toText).join('; ');
  const main = x.risk || x.item || x.title || x.text || x.description || x.summary || x.criterion || x.criteria || x.requirement || x.statement || x.name || '';
  const rest = Object.entries(x).filter(([k, v]) => v && typeof v !== 'object' && v !== main && !['risk', 'item', 'title', 'text', 'description', 'summary', 'criterion', 'criteria', 'requirement', 'statement', 'name'].includes(k));
  return [main, ...rest.map(([k, v]) => `${k[0].toUpperCase()}${k.slice(1)}: ${v}`)].filter(Boolean).join(' — ');
}
// Which round do new questions belong to? A new one (max+1) when nothing is open; the CURRENT round when it is thin
// (fewer than 3 questions), still entirely unanswered, and the new ones fit within 3; otherwise null = refuse.
function topUp(st, incoming) {
  const cur = st.questions.reduce((m, q) => Math.max(m, Number(q.round) || 0), 0);
  const inNew = incoming.filter((x) => !x.precedent).length;
  const open = st.questions.filter(isOpenQ);
  if (!open.length) return inNew <= 3 ? cur + 1 : null;
  const inCur = st.questions.filter((q) => Number(q.round) === cur);
  const untouched = inCur.every(isOpenQ) && open.every((q) => Number(q.round) === cur);
  return untouched && inCur.filter((q) => !q.precedent).length + inNew <= 3 ? cur : null;
}
function checkItem(it, where, errs) {
  const w = `${where} ${it.id || '?'}`;
  if (!it.id || typeof it.id !== 'string') errs.push(`${where}: item needs an id`);
  if (!['Epic', 'Story', 'Task', 'Spike'].includes(it.type)) errs.push(`${w}: type must be Epic|Story|Task|Spike`);
  if (!goodText(it.title)) errs.push(`${w}: needs a real title`);
  if (!goodText(it.description, 10)) errs.push(`${w}: needs a real description`);
  if (!goodText(it.approach, 10)) errs.push(`${w}: needs "approach" (2-4 sentences: how it will be built, components touched)`);
  if (!goodText(it.verification, 8)) errs.push(`${w}: needs "verification" (how the team proves it works)`);
  if (!Array.isArray(it.acceptance_criteria) || !it.acceptance_criteria.length) errs.push(`${w}: needs "acceptance_criteria" (a non-empty list of strings)`);
  if (!FIB.includes(Number(it.size))) errs.push(`${w}: size must be Fibonacci (1,2,3,5,8,13...)`);
  else if (Number(it.size) >= 13) errs.push(`${w}: size ${it.size} is 13+ — split it into smaller items first`);
}

async function patch() {
  const dir = sessionDir();
  const st = loadState(dir);
  let raw;
  if (flags.json && flags.json !== true) raw = flags.json;
  else if (flags.file && flags.file !== true) raw = fs.readFileSync(flags.file, 'utf8');
  else { raw = ''; for await (const c of process.stdin) raw += c; }
  let p;
  try { p = JSON.parse(raw); } catch (e) { die(JSON.stringify({ ok: false, errors: [`invalid JSON: ${e.message}`] }), 2); }
  const errs = [];
  const at = nowIso();

  if (p.ticket && typeof p.ticket === 'object') { const { key, ...rest } = p.ticket; st.ticket = { ...st.ticket, ...rest }; }

  if (p.precedent !== undefined) {
    const pre = p.precedent;
    if (st.precedent || st.questions.some((x) => x.precedent)) errs.push('precedent: already asked once');
    else if (st.questions.some((x) => x.status === 'answered' || x.status === 'deferred')) errs.push('precedent: too late — it must be asked in your first patch, before the interview. Continue without it (do not use precedent unasked).');
    else if (!Array.isArray(pre) || !pre.length) errs.push('precedent: give a non-empty list of {key, summary, outcome?, why?}');
    else if (pre.some((x) => !x || !TICKET_RE.test(String(x.key || '')) || !goodText(x.summary))) errs.push('precedent: every entry needs a ticket key and a summary');
    else {
      const lines = pre.slice(0, 3).map((x) => `- ${x.key} — ${x.summary}${x.outcome ? ` (${toText(x.outcome)})` : ''}${x.why ? `\n  Why similar: ${toText(x.why)}` : ''}`);
      p.questions = [{
        title: 'Use similar finished tickets as the starting point?',
        body: `I found closed tickets that look like this work:\n${lines.join('\n')}\n\nBuilding on them would give a starting breakdown and size references. Nothing is assumed until you decide.`,
        options: ['Use as the template', 'Use only parts of it (say which in your answer)', 'Ignore them — start from scratch'],
        rec: { option: 'A', why: 'Past work on the same kind of change shortens refinement and calibrates sizes; correct anything that differs this time.' },
        precedent: true,
      }, ...(Array.isArray(p.questions) ? p.questions : [])];
      st.precedent = pre.slice(0, 3);
    }
  }

  if (p.questions !== undefined) {
    if (!Array.isArray(p.questions)) errs.push('questions must be an array');
    else if (topUp(st, p.questions) === null) {
      const open = st.questions.filter(isOpenQ);
      errs.push(open.length
        ? `questions: ${open.map((x) => x.id).join(', ')} still wait for the user — ask the next round only after they answer or defer them (send just "reply" and "done" for now)`
        : `questions: at most 3 per round (got ${p.questions.filter((x) => !x.precedent).length}) — ask the most important ones first, the rest next round`);
    } else {
      const maxId = st.questions.reduce((m, q) => Math.max(m, Number(String(q.id).replace(/\D/g, '')) || 0), 0);
      const round = topUp(st, p.questions);
      let n = maxId;
      for (const q0 of p.questions) {
        const q = { ...q0 };
        q.id = q.id || `q${++n}`;
        if (st.questions.some((x) => x.id === q.id)) { errs.push(`question ${q.id} already exists (use "update")`); continue; }
        if (!goodText(q.title)) errs.push(`${q.id}: needs a real title`);
        if (!goodText(q.body, 10)) errs.push(`${q.id}: needs a real body (what hangs on it), not "..."`);
        q.options = normOptions(q.options) || [];
        if (q.rec && q.rec.option != null) q.rec = { ...q.rec, option: resolveOpt(q.options, q.rec.option) };
        if (q.options.some((o) => !goodText(o.text, 2))) errs.push(`${q.id}: every option needs its text`);
        if (q.options.length === 1) errs.push(`${q.id}: give 2-4 options, or none for a free-text question`);
        if (!q.rec || !(goodText(q.rec.why) || goodText(q.rec.text))) errs.push(`${q.id}: needs rec {option, why}`);
        else if (q.options.length && !q.options.some((o) => o.k === q.rec.option)) errs.push(`${q.id}: rec.option must be one of ${q.options.map((o) => o.k).join('/')} (or exactly an option's text)`);
        if (q.topic != null && !(Number.isInteger(q.topic) && q.topic >= 1 && q.topic <= 8)) errs.push(`${q.id}: topic must be 1-8`);
        Object.assign(q, { round: Number(q.round) || round, deps: q.deps || [], status: 'open', thread: [] });
        st.questions.push(q);
      }
    }
  }

  if (p.update) {
    for (const [id, f] of Object.entries(p.update)) {
      const q = st.questions.find((x) => x.id === id);
      if (!q) { errs.push(`update: unknown question ${id}`); continue; }
      if (!isOpenQ(q)) { errs.push(`update: ${id} is ${q.status}; answered questions keep their text`); continue; }
      for (const k of ['title', 'body', 'options', 'rec', 'topic']) if (f[k] !== undefined) q[k] = k === 'options' ? normOptions(f[k]) : f[k];
      if (q.rec && q.rec.option != null) q.rec.option = resolveOpt(q.options || [], q.rec.option);
      if (f.rec && f.updated === undefined) q.updated = true; else if (f.updated !== undefined) q.updated = !!f.updated;
    }
  }

  // Answers exist only when the USER sends them from the page (events.jsonl -> apply). The agent can never answer, defer or
  // "cover" a question for them, or it will (and did) answer its own questions to reach the plan.
  for (const k of ['answer', 'answers', 'covered']) {
    if (p[k] !== undefined) errs.push(`"${k}" is not allowed: only the user answers, on the page. If they typed an answer in the terminal, tell them to click it on the page. To settle a topic, ask a question and let them pick "not applicable".`);
  }

  if (p.reply) {
    for (const [target, text] of Object.entries(p.reply)) {
      const holder = target.startsWith('item:') ? (st.plan?.items || []).find((i) => i.id === target.slice(5)) : st.questions.find((q) => q.id === target);
      if (!holder) { errs.push(`reply: unknown target ${target}`); continue; }
      if (!goodText(text, 2)) { errs.push(`reply ${target}: empty`); continue; }
      (holder.thread = holder.thread || []).push({ who: 'agent', text, at });
    }
  }

  const items = () => (st.plan ? st.plan.items : []);
  if (p.plan || p.planItems) {
    const pq = st.questions.find((x) => x.precedent);
    if (pq && pq.status !== 'answered' && pq.status !== 'deferred') errs.push(`plan: the user has not answered the precedent question (${pq.id}) yet — do not build the plan from precedent before they decide`);
  }
  if (p.plan) {
    const old = new Map(items().map((i) => [i.id, i]));
    const plan = { summary: p.plan.summary, existing: p.plan.existing || [], risks: (p.plan.risks || []).map(toText), items: [] };
    if (!goodText(plan.summary, 10)) errs.push('plan: needs a summary');
    for (const it of p.plan.items || []) {
      checkItem(it, 'plan', errs);
      if (Array.isArray(it.acceptance_criteria)) it.acceptance_criteria = it.acceptance_criteria.map(toText);
      plan.items.push({ acceptance_criteria: [], depends_on: [], ...it, thread: [...(old.get(it.id)?.thread || []), ...(it.thread || [])] });
    }
    if (!plan.items.length) errs.push('plan: needs items');
    st.plan = plan;
  }
  if (p.planItems) {
    if (!st.plan) errs.push('planItems: no plan yet (send "plan" first)');
    else for (const it of p.planItems) {
      const cur = st.plan.items.find((i) => i.id === it.id);
      const merged = { acceptance_criteria: [], depends_on: [], thread: [], ...cur, ...it };
      checkItem(merged, 'planItems', errs);
      if (cur) Object.assign(cur, merged); else st.plan.items.push(merged);
    }
  }
  if (p.removeItems && st.plan) st.plan.items = st.plan.items.filter((i) => !p.removeItems.includes(i.id));
  if (p.risks && st.plan) st.plan.risks = p.risks.map(toText);
  if (st.plan) {
    const ids = new Set(st.plan.items.map((i) => i.id));
    for (const i of st.plan.items) for (const d of i.depends_on || []) if (!ids.has(d) && !TICKET_RE.test(d)) errs.push(`item ${i.id}: depends_on "${d}" is neither a plan item nor a ticket key`);
  }

  if (p.phase !== undefined) {
    if (!['interview', 'review'].includes(p.phase)) errs.push('phase must be interview|review');
    else if (p.phase === 'review') {
      const missing = board(st).topics_missing;
      if (missing.length) errs.push(`phase review: topics ${missing.join(',')} have no answer yet — ask them (a question whose options include "not applicable" settles a topic)`);
      else st.phase = 'review';
    } else st.phase = p.phase;
  }
  if (p.note !== undefined) { if (p.note === null || p.note === '') delete st.note; else st.note = String(p.note); }
  if (p.finished && !st.approved) errs.push('finished: the user has not approved the plan yet (no Approve event). Do not write the plan file or set finished before they click Approve.');
  else if (p.finished) {
    const f = String(p.finished.file || p.finished);
    const abs = path.resolve(projectRoot(dir), f);
    if (!abs.startsWith(projectRoot(dir) + path.sep) || !abs.endsWith('.md') || !fs.existsSync(abs)) errs.push(`finished: ${f} must be an existing .md inside the project folder`);
    else st.finished = { file: path.relative(projectRoot(dir), abs), at };
  }
  // A patch that hands the user something new (questions, plan, reply, finished file) completes the turn on its own:
  // models forget to say `done` and the page then stays locked forever.
  if (p.done === undefined && ['questions', 'plan', 'planItems', 'reply', 'finished', 'phase'].some((k) => p[k] !== undefined)) p.done = true;
  if (p.done !== undefined) {
    // `done` means "I finished everything I applied": the number is ignored (models get it wrong), except that
    // claiming a send that was never applied is refused.
    const seen = Number(st.agent.applied) || 0, n = typeof p.done === 'number' ? p.done : 0;
    if (n > Math.max(seen, Number(st.agent.handled) || 0)) errs.push(`done: ${p.done} is beyond what you applied (${seen}); run apply first`);
    else { st.agent.handled = Math.max(Number(st.agent.handled) || 0, seen); st.agent.status = 'waiting'; st.agent.since = at; }
  }

  if (errs.length) {
    const out = { ok: false, errors: errs, note: 'nothing was changed; fix EVERY error and resend ALL of it (do not drop items to make it pass)' };
    if (errs.some((e) => /^q\d+:/.test(e))) out.question_example = { topic: 2, title: 'Definition of done', body: 'What observable outcome proves this is finished?', options: ['Green CI run', 'Sign-off from QA'], rec: { option: 'A', why: 'Objective and repeatable.' } };
    console.log(JSON.stringify(out)); process.exit(2);
  }
  saveState(dir, st);
  out({ ok: true, board: board(st) });
}

function newSession() {
  const key = flags.ticket;
  if (!key || key === true || !/^[A-Za-z][A-Za-z0-9_]*-\d+$/.test(key)) die('--ticket KEY (e.g. PROJ-123) required');
  const stamp = new Date().toISOString().replace(/\D/g, '').slice(0, 14);
  const dir = path.join(rootDir(), `${key.toUpperCase()}-${stamp}`);
  fs.mkdirSync(dir, { recursive: true });
  const now = new Date().toISOString();
  fs.writeFileSync(path.join(dir, 'state.json'), JSON.stringify({
    ticket: { key: key.toUpperCase() },
    created: now,
    phase: 'interview',
    agent: { status: 'working', since: now, handled: 0 },
    questions: [],
  }, null, 2));
  fs.writeFileSync(path.join(dir, 'events.jsonl'), '');
  out({ session: dir });
}

function serve() {
  const dir = sessionDir();
  const serverFile = path.join(dir, 'server.json');
  const page = () => fs.readFileSync(path.join(HERE, 'page.html'), 'utf8');
  let port = 0;
  let lastPush = null; // { seq, ok, at, error } of the newest push to opencode

  // Reject DNS-rebinding: only accept our own loopback host names.
  const hostOk = (req) => {
    const h = (req.headers.host || '').replace(/:\d+$/, '');
    return h === '127.0.0.1' || h === 'localhost';
  };

  const server = http.createServer((req, res) => {
    if (!hostOk(req)) { res.writeHead(403).end('forbidden'); return; }
    const url = new URL(req.url, 'http://x');
    if (req.method === 'GET' && url.pathname === '/') {
      res.writeHead(200, { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'no-store' }).end(page());
    } else if (req.method === 'GET' && url.pathname === '/api/state') {
      // _seq = newest Send. The page unlocks Send when agent.handled >= _seq, so a stale
      // agent.status can never wedge it.
      let body = '{}';
      try {
        const raw = JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8'));
        // agent said it is done (status waiting) but wrote a stale handled number: trust the applied sends
        if (raw.agent && raw.agent.status === 'waiting' && (raw.agent.applied || 0) > (raw.agent.handled || 0)) raw.agent.handled = raw.agent.applied;
        body = JSON.stringify({ ...withArchive(dir, raw), _seq: readEvents(dir).length, _push: fs.existsSync(path.join(dir, 'push.json')) ? (lastPush || { ok: null }) : null });
      } catch { /* mid-write; page retries */ }
      res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' }).end(body);
    } else if (req.method === 'GET' && url.pathname === '/api/plan') {
      // Serves only the finished plan .md, and only from inside the project root
      // (the folder that holds .jira-refine/), never an arbitrary path from state.json.
      const st = readJson(path.join(dir, 'state.json'), {});
      const f = st.finished && (st.finished.file || st.finished.path);
      const root = path.resolve(dir, '..', '..');
      const abs = f ? path.resolve(root, String(f)) : null;
      if (!abs || !abs.startsWith(root + path.sep) || !abs.endsWith('.md') || !fs.existsSync(abs)) { res.writeHead(404).end('no plan file'); return; }
      res.writeHead(200, { 'content-type': 'application/json', 'cache-control': 'no-store' })
        .end(JSON.stringify({ file: path.relative(root, abs), content: fs.readFileSync(abs, 'utf8') }));
    } else if (req.method === 'POST' && url.pathname === '/api/close') {
      // The page's "Close session" button: only once the plan is written; stops this server.
      if (!/^application\/json/.test(req.headers['content-type'] || '')) { res.writeHead(415).end(); return; }
      if (!readJson(path.join(dir, 'state.json'), {}).finished) { res.writeHead(409).end('plan not finished'); return; }
      res.writeHead(200, { 'content-type': 'application/json' }).end('{"closed":true}', () => {
        out({ type: 'closed' });
        fs.rmSync(serverFile, { force: true });
        setTimeout(() => process.exit(0), 100);
      });
    } else if (req.method === 'POST' && url.pathname === '/api/send') {
      // JSON content-type forces a CORS preflight for cross-origin pages, which we never answer.
      if (!/^application\/json/.test(req.headers['content-type'] || '')) { res.writeHead(415).end(); return; }
      let raw = '';
      req.on('data', (c) => { raw += c; if (raw.length > 1e6) req.destroy(); });
      req.on('end', () => {
        let actions;
        try { actions = JSON.parse(raw).actions; } catch { /* handled below */ }
        if (!Array.isArray(actions) || !actions.length) { res.writeHead(400).end('bad request'); return; }
        const seq = readEvents(dir).length + 1;
        const ev = { type: 'send', seq, at: new Date().toISOString(), session: dir, actions };
        fs.appendFileSync(path.join(dir, 'events.jsonl'), JSON.stringify(ev) + '\n');
        out(brief(ev)); // wakes the agent's Monitor (full event stays in events.jsonl)
        const push = readJson(path.join(dir, 'push.json'), null);
        if (push) {
          deliver(dir, ev, push).then(() => { lastPush = { seq, ok: true, at: ev.at }; })
            .catch(async (e) => {
              // The pinned session may be gone (agent restarted): re-detect once and retry.
              const again = await resolvePush(dir);
              if (again) { fs.writeFileSync(path.join(dir, 'push.json'), JSON.stringify(again)); try { await deliver(dir, ev, again); lastPush = { seq, ok: true, at: ev.at }; return; } catch (e2) { e = e2; } }
              lastPush = { seq, ok: false, at: ev.at, error: String(e.message || e) };
            });
        }
        res.writeHead(200, { 'content-type': 'application/json' }).end(JSON.stringify({ seq }));
      });
    } else {
      res.writeHead(404).end('not found');
    }
  });

  const last = readJson(serverFile, {}).port;
  const listen = (p) => server.listen(p, '127.0.0.1');
  server.on('error', (e) => {
    if (e.code === 'EADDRINUSE' && port !== 0) { port = 0; listen(0); } else die(String(e));
  });
  server.on('listening', () => {
    port = server.address().port;
    fs.writeFileSync(serverFile, JSON.stringify({ port, pid: process.pid, url: `http://127.0.0.1:${port}/` }));
    out({ type: 'ready', url: `http://127.0.0.1:${port}/` });
  });
  port = last || 0;
  listen(port);
  for (const s of ['SIGINT', 'SIGTERM']) process.on(s, () => process.exit(0));
}

// Start `serve` fully detached (own session, no shared stdio) so it outlives the
// caller's shell/tool call, then print the URL. Idempotent: reuses a live server.
async function start() {
  const dir = sessionDir();
  const serverFile = path.join(dir, 'server.json');
  const alive = async (url) => { try { return (await fetch(url + 'api/state', { signal: AbortSignal.timeout(1500) })).ok; } catch { return false; } };
  // (Re)detect opencode every time: the agent that calls `start` is the newest-updated session.
  const setPush = async () => {
    const push = await resolvePush(dir);
    if (push) fs.writeFileSync(path.join(dir, 'push.json'), JSON.stringify(push)); else fs.rmSync(path.join(dir, 'push.json'), { force: true });
    return push ? { mode: 'opencode', ...push } : null;
  };
  const cur = readJson(serverFile, null);
  if (cur && await alive(cur.url)) { out({ type: 'ready', url: cur.url, reused: true, push: await setPush() }); return; }
  fs.rmSync(serverFile, { force: true });
  const log = fs.openSync(path.join(dir, 'serve.log'), 'a');
  const child = spawn(process.execPath, [fileURLToPath(import.meta.url), 'serve', '--foreground', '--session', dir], {
    detached: true, stdio: ['ignore', log, log],
  });
  child.unref();
  for (let i = 0; i < 50; i++) {
    await new Promise((r) => setTimeout(r, 100));
    const s = readJson(serverFile, null);
    if (s && await alive(s.url)) { out({ type: 'ready', url: s.url, pid: s.pid, push: await setPush() }); return; }
  }
  die(`server did not start; see ${path.join(dir, 'serve.log')}`);
}

async function wait() {
  const dir = sessionDir();
  const after = Number(flags.after ?? handledSeq(dir));
  const deadline = Date.now() + Number(flags.timeout || 480) * 1000;
  while (Date.now() < deadline) {
    const next = readEvents(dir).find((e) => e.seq > after);
    if (next) { out(brief(next)); return; }
    await new Promise((r) => setTimeout(r, 1000));
  }
  process.exit(3);
}

function sessions() {
  const root = rootDir();
  if (!fs.existsSync(root)) return;
  const rows = fs.readdirSync(root)
    .map((n) => path.join(root, n))
    .map((dir) => ({ dir, st: readJson(path.join(dir, 'state.json'), null) }))
    .filter((r) => r.st && (flags.all || !r.st.finished))
    .sort((a, b) => (b.st.created || '').localeCompare(a.st.created || ''));
  for (const { dir, st } of rows) {
    const qs = st.questions || [];
    out({
      session: dir, ticket: st.ticket?.key, created: st.created, phase: st.phase,
      open: qs.filter((q) => q.status === 'open' || q.status === 'reopened').length,
      answered: qs.filter((q) => q.status === 'answered').length,
      finished: !!st.finished,
    });
  }
}

switch (cmd) {
  case 'new': newSession(); break;
  // `serve` detaches by default so no launch style (shell `&`, tool call end) can kill it;
  // `--foreground` is for a Monitor that reads Sends from stdout.
  case 'serve': if (flags.foreground) serve(); else await start(); break;
  case 'start': await start(); break;
  case 'url': {
    const s = readJson(path.join(sessionDir(), 'server.json'), null);
    if (!s) die('server not started for this session');
    console.log(s.url);
    break;
  }
  case 'pending': {
    const dir = sessionDir();
    const h = handledSeq(dir);
    for (const e of readEvents(dir)) if (e.seq > h) out(brief(e));
    break;
  }
  case 'wait': await wait(); break;
  case 'apply': apply(); break;
  case 'patch': await patch(); break;
  case 'sessions': sessions(); break;
  default: die('usage: server.mjs new|serve|start|url|pending|wait|apply|patch|sessions (see header comment)');
}
