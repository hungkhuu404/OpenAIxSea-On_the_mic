import assert from 'node:assert/strict';
import { sessionView } from '../ui/session.js';
import { sessionTools } from '../ui/session-tools.js';
import { clipScene, subtitleLines } from '../ui/clip.js';
import { setPresenter } from '../ui/data.js';

class Node {
  constructor(tag = '') { this.tagName = tag; this._text = ''; this.children = []; this.dataset = {}; this.classes = new Set(); this.classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name) }; }
  set textContent(text) { this._text = text; this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  setAttribute(key, value) { this[key] = value; }
  scrollIntoView() { this.scrolled = true; }
  focus() { this.focused = true; }
  pause() { this.paused = true; }
}
globalThis.document = { createElement: tag => new Node(tag), createTextNode: text => Object.assign(new Node(), { textContent: text }) };
globalThis.requestAnimationFrame = callback => callback();
const step = { id: 1, type: 'step', payload: { session_id: 'clip-a1', turn_id: 't02', step: 'S1', outcome: 'extracted', ms: 10 } };
const session = { session_id: 'synthetic', sku_pin: 'kera', as_of: '2025-01-08', channel: 'livestream',
  turns: [{ turn_id: 't01', text: 'First claim' }, { turn_id: 't02', text: 'Second claim', display_speaker: 'Speaker unknown' }],
  atoms: [], verdicts: [], flags: [{ message: 'Synthetic session flag' }], events: [] };
const sent = [], navigated = [];
const clip = { ...session, session_id: 'clip-a1', channel: 'video',
  turns: session.turns.map((turn, i) => ({ ...turn, start_s: i * 2, end_s: i * 2 + 2, display_speaker: 'Speaker: unidentified' })),
  speaker_line: 'Quote attributed by the press to [TEST SPEAKER]', source_line: 'Synthetic source' };
globalThis.fetch = async (url, options) => {
  let value;
  if (options?.method === 'POST') { sent.push(JSON.parse(options.body)); value = {}; }
  else if (url.includes('/api/ui/kera')) value = { recall_date: '2025-03-24' };
  else if (url.includes('/api/search')) value = [
    { turn_id: 't02', display_speaker: 'Speaker unknown', snippet: 'Second claim' },
    { turn_id: 't01', display_speaker: '[TEST ROLE]', snippet: 'First claim' }];
  else if (url.includes('/api/sessions/clip-')) value = structuredClone(clip);
  else if (url.includes('/api/events')) value = [step];
  else if (url.includes('.subs.json')) value = [{ start: 2, end: 4, vi: 'Synthetic subtitle', en: 'English subtitle' }];
  else value = {};
  return { ok: true, json: async () => value };
};
const view = sessionView(session);
assert.match(view.node.children[0].textContent, /Synthetic session flag/);
const tools = await sessionTools(session, view, id => navigated.push(id));
const dates = tools.children[0];
dates.children[0].onclick(); assert.deepEqual(navigated, ['kera-reenact-2024-12-12']);
await dates.children[2].onclick();
assert.deepEqual(sent[0], { sku_id: 'kera', recall_date: '2025-03-24', source: 'HCMC Food Safety Department decision, 24/03/2025 (replay)' });
const input = tools.children[1].children[0]; input.value = 'dia rau';
const key = key => ({ key, preventDefault() {} });
await input.onkeydown(key('Enter'));
const turns = view.node.children[2].children[0];
assert.equal(turns.children[1]['aria-pressed'], 'true'); assert.equal(turns.children[1].scrolled, true);
assert.match(tools.textContent, /Speaker unknown/);
await input.onkeydown(key('ArrowDown')); assert.equal(turns.children[0]['aria-pressed'], 'true');
await input.onkeydown(key('ArrowUp')); assert.equal(turns.children[1]['aria-pressed'], 'true');
const originalFetch = globalThis.fetch, pending = {};
globalThis.fetch = url => new Promise(resolve => { pending[url.includes('q=old') ? 'old' : 'new'] = resolve; });
input.value = 'old'; const oldSearch = input.onkeydown(key('Enter'));
input.value = 'new'; const newSearch = input.onkeydown(key('Enter'));
pending.new({ ok: true, json: async () => [{ turn_id: 't01', display_speaker: 'Speaker unknown', snippet: 'new result' }] });
await newSearch;
pending.old({ ok: true, json: async () => [{ turn_id: 't02', display_speaker: 'Speaker unknown', snippet: 'old result' }] });
await oldSearch;
assert.equal(turns.children[0]['aria-pressed'], 'true');
assert.doesNotMatch(tools.textContent, /old result/);
globalThis.fetch = originalFetch;
view.event({ type: 'recall.applied', payload: { sessions: [{ session_id: 'synthetic', flagged_atoms: 2 }] } });
assert.match(view.node.textContent, /2 flagged atoms · No human needed/);

assert.ok(subtitleLines('word '.repeat(30)).split('\n').every(line => line.length <= 42));
assert.ok(subtitleLines('x'.repeat(100)).split('\n').every(line => line.length <= 42));
setPresenter(true);
const root = new Node(); let ended = 0;
const dispose = await clipScene(root, 'a1', { clips: { a1: { available: true, subtitles: true } }, translations: {} }, () => { ended++; });
const layout = root.children[0], left = layout.children[0], right = layout.children[1];
const video = left.children[0];
video.currentTime = 3; video.ontimeupdate();
assert.match(left.textContent, /Synthetic subtitle/);
assert.match(right.textContent, /Claims · t02/);
assert.doesNotMatch(right.textContent, /extracted/);
video.currentTime = 4; video.ontimeupdate(); assert.match(right.textContent, /extracted/);
video.currentTime = 1; video.ontimeupdate(); assert.doesNotMatch(right.textContent, /extracted/);
video.onended(); assert.equal(ended, 1); assert.equal(right.hidden, true);
dispose(); assert.equal(video.paused, true);
const backupRoot = new Node();
await clipScene(backupRoot, 'a2', { clips: {}, translations: {}, video_mode: true }, () => {});
assert.match(backupRoot.textContent, /Clip shown live on the presenting laptop only/);
console.log('P4 recall, search, subtitles, clip timing and video-mode checks passed');
