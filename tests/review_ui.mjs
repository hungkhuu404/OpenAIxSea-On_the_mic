import assert from 'node:assert/strict';
import { queueGroups, draftTitle, provenanceCard, whoRow, reviewView } from '../ui/review.js';

// Minimal DOM values for module behavior tests; no browser or rendering engine.
class Node {
  constructor(tag = '', text = '') { this.tagName = tag; this._text = text; this.children = []; this.dataset = {}; this.value = ''; this.disabled = false; }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  setAttribute(key, value) { this[key] = value; }
  focus() { this.focused = true; }
  querySelectorAll(selector) { return this.children.flatMap(n => [n, ...n.querySelectorAll(selector)]).filter(n => selector === '[data-rule]' ? n.dataset.rule : selector === '[data-merge]' ? n.dataset.merge : false); }
  querySelector(selector) { return this.querySelectorAll(selector)[0]; }
}
globalThis.document = { createElement: tag => new Node(tag), createTextNode: text => new Node('', text) };
const item = (id, pattern = 'phòng … bệnh') => ({ atom_key: id, cluster_key: 'demo:DOSSIER_EMPTY', atom: { span: 'phòng các bệnh', near_hits: pattern ? [{ pattern, law_anchor: 'LDUOC-2016/D6/K15' }] : [] }, verdict: { verdict: 'abstain', ts: '2026-10-21T09:00:00Z' }, chips: [], score: 4, score_max: 28, source: 'Synthetic' });
assert.equal(queueGroups([item('a'), item('b')]).length, 2);
assert.equal(queueGroups([item('a'), item('b'), item('c')]).length, 1);
assert.equal(queueGroups([item('a'), item('b'), item('c', 'chữa … bệnh')]).length, 3);
const overlap = item('overlap'); overlap.atom.near_hits.push({ pattern: 'chữa … bệnh', law_anchor: 'LDUOC-2016/D6/K15' });
assert.equal(queueGroups([overlap, item('b'), item('c')]).length, 1);
assert.equal(draftTitle('error'), 'Draft error');
const meta = { origin: 't5', approved_by: null, attempts: [], anchors: ['LDUOC-2016/D6/K15'] };
const job = { job_id: 'fake-job', status: 'passed', dry: true, config: 'abc', rule_id: 'R04_DEMO', cluster_item_ids: ['a'], attempts: [{ started: '2026-10-21T09:00:00Z', gate_result: 'pass', error_lines: ['synthetic error'], files_read: ['rules/R04_DEMO.py'] }] };
const rule = { meta, jobs: [job], runs: [], attempts: job.attempts, sources: [{ text: 'Một câu', source_note: 'Constructed sentence', ts: '2026-10-21T09:00:00Z' }], law: [{ anchor: 'LDUOC-2016/D6/K15', text: 'Cấm chữa bệnh', law_span: 'chữa bệnh' }], funnel: { K: 1, I: 1, J: 0, R: 0 } };
const counts = { code: 1, llm_votes: 2, human: 3, denominator: 6, measured_at: '2026-10-21T09:00:00Z', source: 'ledger' };
let sent = [], refreshes = 0;
globalThis.fetch = async (url, options) => {
  if (options?.method === 'POST') { sent.push(JSON.parse(options.body)); return { ok: true, json: async () => ({ version: 2 }) }; }
  let value;
  if (url.includes('/dryrun')) value = { changed_atoms: 2, changed_cases: 1, changed_golden_outside_sources: 1, observed_changes: [{}, {}], flag_many: true, scope: { cases: 2, claims: 4, golden_cases: 10, measured_at: '2026-10-21T09:00:00Z' } };
  else if (url === '/api/rules') value = [{ rule_id: 'R04', meta }];
  else if (url === '/api/jobs') value = [job];
  else if (url.includes('/review-stage')) value = { pr1_job_id: 'fake-job', pr2: { runs: [{ ...job, rule_id: 'R04_DEMO', result: 'failed', started: '2026-10-21T09:00:00Z' }] } };
  else if (url === '/api/pending') value = [];
  else if (url === '/api/queue') value = [item('a')];
  else if (url === '/api/who-decided') value = { live: counts, oracle: null };
  else value = rule;
  return { ok: true, json: async () => value };
};
const card = await provenanceCard('R04_DEMO', { approver: 'operator', refresh: async () => { refreshes++; } });
assert.match(card.textContent, /DRY RUN/);
assert.match(card.textContent, /Constructed sentence/);
assert.match(card.textContent, /synthetic error/);
assert.match(card.textContent, /Files Codex read/);
assert.match(card.textContent, /If merged: 2 old verdicts/);
assert.match(card.textContent, /Warning:/);
const merge = card.querySelector('[data-merge]');
assert.equal(merge.disabled, false);
await merge.onclick(); assert.deepEqual(sent[0], { approved_by: 'operator' }); assert.equal(refreshes, 1);
const publicCard = await provenanceCard('R04_DEMO', { publicMode: true });
assert.equal(publicCard.querySelector('[data-merge]').disabled, true);
const failedCard = await provenanceCard('R04_DEMO', { job: { ...job, status: 'failed' } });
assert.match(failedCard.textContent, /Draft failed/); assert.equal(failedCard.querySelector('[data-merge]').disabled, true);
const who = whoRow({ live: counts, oracle: { standin: true, who_decided_v1: { ...counts, source: 'oracle' }, who_decided_v2: null } });
assert.match(who.textContent, /1\/6/); assert.match(who.textContent, /stand-in split of golden/); assert.match(who.textContent, /live, this session/);
const view = await reviewView({ target: 'a' });
const queueButton = view.node.children[0].children[1].children[0]; await queueButton.onclick();
const labelPanel = view.node.children[1].children[1];
const picker = labelPanel.children.find(n => n['aria-label'] === 'Rule');
assert.equal(picker.value, 'R04');
const labeler = labelPanel.children.find(n => n.tagName === 'label' && n.textContent.startsWith('Labeler')).children[0]; labeler.value = 'operator';
const record = labelPanel.children.find(n => n.tagName === 'label' && n.textContent.includes('record only')).children[0]; record.checked = true;
await labelPanel.children.find(n => n.tagName === 'button' && n.textContent === 'Confirm label').onclick();
assert.equal(sent.at(-1).auto_trigger, false);
assert.equal(sent.at(-1).label, 'abstain');
// A draft may fail before a rule module exists. The attempts must remain visible.
globalThis.fetch = async () => ({ ok: false, status: 404, headers: { get: () => null }, json: async () => ({}) });
const missing = await provenanceCard('R04_MISSING', { job: { ...job, status: 'error' } });
assert.match(missing.textContent, /Draft error/); assert.match(missing.textContent, /synthetic error/);
console.log('Review module behavior passed');
