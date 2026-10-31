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
}
globalThis.document = { createElement: tag => new Node(tag), createTextNode: text => Object.assign(new Node(), { textContent: text }) };
globalThis.requestAnimationFrame = callback => callback();
const session = {
  session_id: 'synthetic', source: 'Synthetic', public_mode: true,
  turns: [{ turn_id: 't01', text: 'First line' }, { turn_id: 't02', text: 'Second line' }],
  atoms: [{ atom_id: 't02.a1', turn_id: 't02', span: 'Second line' }],
  verdicts: [{ atom_key: 'synthetic:t02.a1', verdict: 'abstain', reason_code: 'LLM_OFF' }],
};
const step = (id, outcome) => ({ id, type: 'step', payload: { session_id: 'synthetic', turn_id: 't02', step: 'S1', outcome, ms: 12 } });
const view = sessionView(session, {}, [step(1, 'extracted')]);
const selected = () => view.node.children[2].children[0].children.find(n => n['aria-pressed'] === 'true')?.dataset.turn;
view.node.children[2].children[0].children[1].onclick();
assert.equal(selected(), 't02');
assert.match(view.node.textContent, /extracted/);
const updated = { ...session, verdicts: [{ ...session.verdicts[0], verdict: 'red', reason_code: 'UPDATED' }] };
view.update(updated);
assert.equal(selected(), 't02');
assert.match(view.node.textContent, /extracted/);
assert.match(view.node.textContent, /UPDATED/);
view.event(step(4, 'newest step'));
view.event(step(1, 'outdated step'));
assert.match(view.node.textContent, /newest step/);
assert.doesNotMatch(view.node.textContent, /outdated step/);

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
console.log('Session selection, pipeline history and event refresh passed');
