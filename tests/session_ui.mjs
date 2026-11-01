import assert from 'node:assert/strict';
import { sessionView } from '../ui/session.js';

// Small DOM values exercise the real modules without a browser/rendering engine.
class Node {
  constructor(tag = '') { this.tagName = tag; this._text = ''; this.children = []; this.dataset = {}; this.classList = { add() {}, remove() {} }; }
  set textContent(value) { this._text = value; this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  setAttribute(key, value) { this[key] = value; }
  focus() { document.activeElement = this; }
  scrollIntoView(options) { this.lastScroll = options; }
}
globalThis.document = { createElement: tag => new Node(tag), createTextNode: text => Object.assign(new Node(), { textContent: text }) };
globalThis.requestAnimationFrame = callback => callback();
const find = (node, predicate) => predicate(node) ? node : node.children.map(child => find(child, predicate)).find(Boolean);
const byClass = (node, name) => find(node, child => (child.className || '').split(' ').includes(name));
const session = {
  session_id: 'synthetic', source: 'Synthetic', public_mode: true,
  turns: [{ turn_id: 't01', text: 'First line' }, { turn_id: 't02', text: 'Second line' }],
  atoms: [{ atom_id: 't02.a1', turn_id: 't02', span: 'Second line' }],
  verdicts: [{ atom_key: 'synthetic:t02.a1', verdict: 'abstain', reason_code: 'LLM_OFF' }],
};
const step = (id, outcome) => ({ id, type: 'step', payload: { session_id: 'synthetic', turn_id: 't02', step: 'S1', outcome, ms: 12 } });
const view = sessionView(session, {}, [step(1, 'extracted')]);
const selected = () => byClass(view.node, 'turn-list').children.find(n => n['aria-pressed'] === 'true')?.dataset.turn;
byClass(view.node, 'turn-list').children[1].onclick();
assert.equal(selected(), 't02');
assert.match(view.node.textContent, /extracted/);
const pipeline = byClass(view.node, 'pipeline');
pipeline.open = true;
byClass(view.node, 'turn-list').children[1].focus();
const updated = { ...session, verdicts: [{ ...session.verdicts[0], verdict: 'red', reason_code: 'UPDATED' }] };
view.update(updated);
assert.equal(selected(), 't02');
assert.equal(document.activeElement, byClass(view.node, 'turn-list').children[1], 'refresh preserves transcript keyboard focus');
assert.equal(byClass(view.node, 'pipeline'), pipeline);
assert.equal(pipeline.open, true, 'refresh preserves the open processing trace');
assert.match(view.node.textContent, /extracted/);
assert.match(view.node.textContent, /UPDATED/);
const evidenceCard = byClass(view.node, 'claim-card');
view.event(step(4, 'newest step'));
view.event(step(1, 'outdated step'));
assert.equal(byClass(view.node, 'claim-card'), evidenceCard, 'step telemetry leaves evidence controls intact');
assert.match(view.node.textContent, /newest step/);
assert.doesNotMatch(view.node.textContent, /outdated step/);
globalThis.matchMedia = () => ({ matches: true });
view.selectTurn('t02', true);
assert.equal(byClass(view.node, 'turn-list').children[1].lastScroll.behavior, 'auto', 'search navigation respects reduced motion');

const { watchSession } = await import('../ui/session-events.js');
let receive, calls = 0, release, unsubscribed = false;
const stop = watchSession(view, session.session_id, {
  publicMode: false, active: () => true,
  subscribe: (_public, callback) => { receive = callback; return () => { unsubscribed = true; }; },
  load: async () => { calls++; return new Promise(resolve => { release = resolve; }); },
});
await receive({ type: 'verdict', payload: { session_id: 'another' } });
assert.equal(calls, 0);
assert.equal(selected(), 't02');
const refreshing = receive({ type: 'verdict', payload: { session_id: 'synthetic' } });
await receive(step(5, 'during refresh'));
await receive({ type: 'turn.verdict', payload: { session_id: 'synthetic' } });
release(updated);
await new Promise(resolve => setImmediate(resolve));
assert.equal(calls, 2, 'an event arriving during a request must get a fresh read');
release(session);
await refreshing;
assert.equal(selected(), 't02');
assert.match(view.node.textContent, /during refresh/);
const globalRefresh = receive({ type: 'backfill.done', payload: { version: 2 } });
assert.equal(calls, 3, 'backfill invalidates all displayed sessions');
stop();
release(updated);
await globalRefresh;
assert.equal(unsubscribed, true);
assert.doesNotMatch(view.node.textContent, /UPDATED/, 'a stopped subscription must not apply a late response');

view.update({ ...session, turns: [session.turns[0]], atoms: [], verdicts: [] });
assert.equal(selected(), 't01', 'fall back only when the selected turn was removed');
view.update({ ...session, turns: [], atoms: [], verdicts: [] });
assert.equal(selected(), undefined);
assert.match(view.node.textContent, /No turns/);
const summaryView = sessionView({ ...session,
  sku_pin: 'sample_product', as_of: '2026-10-21', channel: 'written',
  atoms: [...session.atoms, { atom_id: 't02.a2', turn_id: 't02', span: 'line' }],
  verdicts: [
    { ...session.verdicts[0], verdict_id: 2, verdict: 'red' },
    { ...session.verdicts[0], verdict_id: 1, verdict: 'green' },
    { atom_key: 'synthetic:removed.a1', verdict_id: 3, verdict: 'abstain' },
  ],
});
const summary = value => find(summaryView.node, node => node.dataset.verdict === value);
assert.equal(byClass(summary('red'), 'summary-count').textContent, '1');
assert.equal(byClass(summary('green'), 'summary-count').textContent, '0', 'superseded decisions do not inflate totals');
assert.equal(byClass(summary('abstain'), 'summary-count').textContent, '0', 'removed and undecided atoms do not become Needs human');
assert.match(summaryView.node.textContent, /1 of 2 claims have a recorded decision/);
assert.match(summaryView.node.textContent, /sample_product/);
assert.match(summaryView.node.textContent, /2026-10-21/);
summaryView.selectTurn('t02');
assert.match(summaryView.node.textContent, /1 claim is awaiting a recorded decision/);
assert.match(byClass(summaryView.node, 'claim-card')['aria-label'], /Prohibited/);
// An abandoned route must not publish metadata or subscribe after its load finishes.
const { sessionScreen } = await import('../ui/session-screen.js');
const header = new Node(); header.textContent = 'REVIEW';
document.querySelector = () => header;
let subscriptions = 0;
globalThis.EventSource = class {
  constructor() { subscriptions++; }
  addEventListener() {}
  close() {}
};
const response = value => ({ ok: true, json: async () => value });
let resolveSession;
globalThis.fetch = url => {
  if (url === '/api/sessions/synthetic') return new Promise(resolve => { resolveSession = resolve; });
  return Promise.resolve(response(url.startsWith('/api/events') ? [] : {}));
};
const abandonedRoot = new Node();
const abandonedScreen = sessionScreen(abandonedRoot, 'synthetic');
abandonedRoot.children[0].isConnected = false;
resolveSession(response(structuredClone(session)));
const closeAbandoned = await abandonedScreen;
assert.equal(header.textContent, 'REVIEW', 'an old session load must not overwrite the current route header');
assert.equal(subscriptions, 0, 'an abandoned session must not start an event subscription');
assert.equal(abandonedRoot.children[0].children.length, 0);
closeAbandoned();

// The tools request is a second asynchronous boundary after the session data.
let resolveTools, toolsRequested;
const waitingForTools = new Promise(resolve => { toolsRequested = resolve; });
globalThis.fetch = url => {
  if (url === '/api/sessions/synthetic') return Promise.resolve(response({ ...structuredClone(session), sku_pin: 'kera' }));
  if (url === '/api/ui/kera') return new Promise(resolve => { resolveTools = resolve; toolsRequested(); });
  return Promise.resolve(response(url.startsWith('/api/events') ? [] : {}));
};
const toolRoot = new Node();
const toolScreen = sessionScreen(toolRoot, 'synthetic');
await waitingForTools;
toolRoot.children[0].isConnected = false;
resolveTools(response({ recall_date: '2025-03-24' }));
const closeTools = await toolScreen;
assert.equal(header.textContent, 'REVIEW', 'late session tools must not overwrite the current route header');
assert.equal(subscriptions, 0);
assert.equal(toolRoot.children[0].children.length, 0);
closeTools();
const navigated = [];
globalThis.fetch = url => Promise.resolve(response(url === '/api/sessions/synthetic' ?
  { ...structuredClone(session), fixture: true, sku_pin: 'kera' } : { recall_date: '2025-03-24' }));
const navigationRoot = new Node();
const closeNavigation = await sessionScreen(navigationRoot, 'synthetic', {}, { onNavigate: id => navigated.push(id) });
byClass(navigationRoot, 'date-bar').children[0].onclick();
assert.deepEqual(navigated, ['kera-reenact-2024-12-12'], 'ledger date changes must use the route callback to keep the session picker and URL synchronized');
closeNavigation();
console.log('Session selection, decision totals, accessible refresh, pipeline history and abandoned routes passed');
