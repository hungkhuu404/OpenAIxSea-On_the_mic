import assert from 'node:assert/strict';
import { sessionView } from '../ui/session.js';
import { sessionTools } from '../ui/session-tools.js';
import { clipScene, subtitleLines } from '../ui/clip.js';
import { setPresenter } from '../ui/data.js';
import { sessionScreen } from '../ui/session-screen.js';

class Node {
  constructor(tag = '') { this.tagName = tag; this._text = ''; this.children = []; this.dataset = {}; this.classes = new Set(); this.classList = { add: name => this.classes.add(name), remove: name => this.classes.delete(name) }; this.paused = true; this.currentTime = 0; this.volume = 1; this.seeking = false; this.ended = false; }
  set textContent(text) { this._text = text; this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(''); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = nodes; this._text = ''; }
  setAttribute(key, value) { this[key] = value; }
  scrollIntoView() { this.scrolled = true; }
  focus() { this.focused = true; }
  async play() { this.paused = false; this.ended = false; this.onplay?.(); }
  pause() { this.paused = true; this.onpause?.(); }
}
globalThis.document = { createElement: tag => new Node(tag), createTextNode: text => Object.assign(new Node(), { textContent: text }), querySelector: () => new Node() };
globalThis.requestAnimationFrame = callback => callback();
const findClass = (node, name) => node.className?.split(' ').includes(name) ? node : node.children.map(child => findClass(child, name)).find(Boolean);
const findTag = (node, tag) => node.tagName === tag ? node : node.children.map(child => findTag(child, tag)).find(Boolean);
const findButton = (node, label) => node.tagName === 'button' && label.test(node.textContent) ? node : node.children.map(child => findButton(child, label)).find(Boolean);
const flush = () => new Promise(resolve => setImmediate(resolve));
const step = { id: 1, type: 'step', payload: { session_id: 'clip-a1', turn_id: 't02', step: 'S1', outcome: 'extracted', ms: 10 } };
const session = { session_id: 'synthetic', sku_pin: 'kera', as_of: '2025-01-08', channel: 'livestream',
  turns: [{ turn_id: 't01', text: 'First claim' }, { turn_id: 't02', text: 'Second claim', display_speaker: 'Speaker unknown' }],
  atoms: [], verdicts: [], flags: [{ message: 'Synthetic session flag' }], events: [] };
const sent = [], navigated = [];
const clip = { ...session, session_id: 'clip-a1', channel: 'video',
  turns: session.turns.map((turn, i) => ({ ...turn, start_s: i * 2, end_s: i * 2 + 2, display_speaker: 'Speaker: unidentified' })),
  atoms: [
    { atom_id: 't01.a1', turn_id: 't01', span: 'First claim' },
    { atom_id: 't02.a1', turn_id: 't02', span: 'Second claim' }],
  verdicts: [
    { verdict_id: 1, atom_key: 'clip-a1:t01.a1', verdict: 'red', decided_by: 'rule:TEST_RULE', legal_basis: [], flags: [] },
    { verdict_id: 2, atom_key: 'clip-a1:t02.a1', verdict: 'green', decided_by: 'allow_list', legal_basis: [], flags: [] }],
  speaker_line: 'Quote attributed by the press to [TEST SPEAKER]', source_line: 'Synthetic source' };
const processRequests = [];
let deferProcessing = false, loadedClip = clip;
globalThis.fetch = async (url, options) => {
  let value;
  if (deferProcessing && url.includes('/process') && options?.method === 'POST') {
    const body = JSON.parse(options.body);
    return new Promise(resolve => processRequests.push({ url, body, resolve }));
  }
  if (options?.method === 'POST') { sent.push(JSON.parse(options.body)); value = {}; }
  else if (url.includes('/api/ui/kera')) value = { recall_date: '2025-03-24' };
  else if (url.includes('/api/search')) value = [
    { turn_id: 't02', display_speaker: 'Speaker unknown', snippet: 'Second claim' },
    { turn_id: 't01', display_speaker: '[TEST ROLE]', snippet: 'First claim' }];
  else if (url.includes('/api/sessions/clip-')) value = structuredClone(loadedClip);
  else if (url.includes('/api/sessions/kera-')) value = structuredClone({ ...session, fixture: true });
  else if (url.includes('/api/events')) value = [step];
  else if (url.includes('.subs.json')) value = [{ start: 2, end: 4, vi: 'Second claim', en: 'English subtitle' }];
  else value = {};
  return { ok: true, json: async () => value };
};
const view = sessionView(session);
assert.match(view.node.textContent, /Synthetic session flag/);
const tools = await sessionTools(session, view, id => navigated.push(id));
const dates = findClass(tools, 'date-bar');
dates.children[0].onclick(); assert.deepEqual(navigated, ['kera-reenact-2024-12-12']);
await dates.children[2].onclick();
assert.deepEqual(sent[0], { sku_id: 'kera', recall_date: '2025-03-24', source: 'HCMC Food Safety Department decision, 24/03/2025 (replay)' });
const searchForm = findClass(tools, 'search-form'), searchControls = findClass(tools, 'search-controls');
const input = searchControls.children[0], searchButton = searchControls.children[1]; input.value = 'dia rau';
const searchResults = findClass(tools, 'search-results'), searchStatus = findClass(tools, 'search-status');
const key = key => ({ key, preventDefault() {} });
await input.onkeydown(key('Enter'));
const turns = findClass(view.node, 'turn-list');
assert.equal(turns.children[1]['aria-pressed'], 'true'); assert.equal(turns.children[1].scrolled, true);
assert.match(tools.textContent, /Speaker unknown/);
await input.onkeydown(key('ArrowDown')); assert.equal(turns.children[0]['aria-pressed'], 'true');
await input.onkeydown(key('ArrowUp')); assert.equal(turns.children[1]['aria-pressed'], 'true');
const originalFetch = globalThis.fetch, pending = {};
globalThis.fetch = url => new Promise(resolve => { pending[url.includes('q=old') ? 'old' : 'new'] = resolve; });
input.value = 'old'; const oldSearch = input.onkeydown(key('Enter'));
assert.equal(searchResults.children.length, 0, 'A new search clears the previous hits immediately');
assert.equal(searchButton.disabled, true);
assert.match(searchStatus.textContent, /Searching/);
await input.onkeydown(key('ArrowDown')); assert.equal(turns.children[1]['aria-pressed'], 'true');
input.value = 'new'; const newSearch = input.onkeydown(key('Enter'));
pending.new({ ok: true, json: async () => [{ turn_id: 't01', display_speaker: 'Speaker unknown', snippet: 'new result' }] });
await newSearch;
pending.old({ ok: true, json: async () => [{ turn_id: 't02', display_speaker: 'Speaker unknown', snippet: 'old result' }] });
await oldSearch;
assert.equal(turns.children[0]['aria-pressed'], 'true');
assert.doesNotMatch(tools.textContent, /old result/);
input.value = 'new'; const abandonedSearch = input.onkeydown(key('Enter'));
input.value = 'edited'; input.oninput();
input.value = 'new'; input.oninput();
pending.new({ ok: true, json: async () => [{ turn_id: 't02', display_speaker: 'Speaker unknown', snippet: 'abandoned result' }] });
await abandonedSearch;
assert.equal(searchResults.children.length, 0, 'Editing invalidates the pending search even when the query is typed again');
assert.equal(searchStatus.textContent, '');
await input.onkeydown(key('ArrowDown')); assert.equal(turns.children[0]['aria-pressed'], 'true');
const emptySearch = searchForm.onsubmit(key('Enter'));
pending.new({ ok: true, json: async () => [] }); await emptySearch;
assert.match(searchStatus.textContent, /No matches/);
assert.equal(searchButton.disabled, false);
input.value = ''; input.oninput();
assert.equal(searchResults.children.length, 0);
assert.equal(searchStatus.textContent, '');
assert.equal(searchButton.disabled, true);
globalThis.fetch = originalFetch;
view.event({ type: 'recall.applied', payload: { sessions: [{ session_id: 'synthetic', flagged_atoms: 2 }] } });
assert.match(view.node.textContent, /2 flagged atoms · No human needed/);

assert.ok(subtitleLines('word '.repeat(30)).split('\n').every(line => line.length <= 42));
assert.ok(subtitleLines('x'.repeat(100)).split('\n').every(line => line.length <= 42));
setPresenter(true);
deferProcessing = true;
async function resolveProcess(request, ok = true) {
  const atoms = loadedClip.atoms.filter(atom => atom.turn_id === request.body.turn_id);
  const keys = new Set(atoms.map(atom => `${loadedClip.session_id}:${atom.atom_id}`));
  request.resolve({ ok, status: ok ? 200 : 503, headers: { get: () => null }, json: async () => ok ? {
    session: loadedClip, atoms, verdicts: loadedClip.verdicts.filter(row => keys.has(row.atom_key)), flags: [],
  } : {} });
  await flush();
}
function seek(video, time) {
  video.currentTime = time; video.seeking = true; video.ended = false;
  video.onseeking?.(); video.ontimeupdate();
  video.seeking = false; video.onseeked?.();
}
const root = new Node(); let ended = 0;
const dispose = await clipScene(root, 'a1', { clips: { a1: { available: true, subtitles: true } }, translations: {} }, () => { ended++; });
const layout = root.children[0], left = layout.children[0], right = layout.children[1];
const video = findTag(left, 'video'), flow = findClass(right, 'clip-flow');
const spoken = findClass(flow, 'clip-spoken'), evidence = findClass(flow, 'clip-evidence');
const monitor = findClass(left, 'monitor-shell'), timecode = findClass(monitor, 'monitor-timecode');
const monitorState = findClass(monitor, 'monitor-state');
assert.match(monitor.textContent, /LIVE REVIEW/);
assert.match(monitor.textContent, /RECORDED CLIP/, 'The review monitor must identify its recorded video source');
assert.equal(timecode.textContent, '00:00:00');
assert.match(monitorState.textContent, /Ready/i);
assert.doesNotMatch(right.textContent, /First claim|Second claim|Prohibited|Within file/,
  'Preloaded transcript and decisions must stay hidden before playback');
assert.equal(processRequests.length, 0);
await video.play();
video.currentTime = 1; video.ontimeupdate();
assert.equal(timecode.textContent, '00:00:01');
assert.match(monitorState.textContent, /Playing/i);
assert.match(spoken.textContent, /First claim/);
assert.doesNotMatch(evidence.textContent, /Prohibited|Within file/);
assert.equal(processRequests.length, 0, 'Do not evaluate an unfinished line');
video.currentTime = 2.1; video.ontimeupdate();
assert.equal(processRequests.length, 1);
assert.deepEqual(processRequests[0].body, { turn_id: 't01', reprocess: true });
assert.match(processRequests[0].url, /\/api\/sessions\/clip-a1\/process/);
assert.match(spoken.textContent, /Second claim/);
video.currentTime = 2.2; video.ontimeupdate();
video.pause();
assert.equal(timecode.textContent, '00:00:02');
assert.match(monitorState.textContent, /Paused/i);
assert.match(spoken.textContent, /Second claim/, 'Pausing keeps the current transcript');
await video.play();
video.currentTime = 3; video.ontimeupdate();
assert.equal(processRequests.length, 1, 'Repeated playback updates must not duplicate an in-flight request');
assert.match(findClass(left, 'clip-subtitles').textContent, /Second claim/);
assert.match(findClass(left, 'clip-subtitles').textContent, /English subtitle/);
assert.doesNotMatch(evidence.textContent, /Prohibited|Within file/);
video.currentTime = 4; video.ontimeupdate();
assert.equal(processRequests.length, 1, 'Completed turns are evaluated sequentially');
video.ended = true; video.paused = true; video.onended();
assert.equal(timecode.textContent, '00:00:04');
assert.match(monitorState.textContent, /Ended/i);
assert.equal(ended, 0, 'The clip must not navigate away automatically');
assert.notEqual(right.hidden, true);
await resolveProcess(processRequests[0]);
assert.equal(processRequests.length, 2);
assert.deepEqual(processRequests[1].body, { turn_id: 't02', reprocess: true });
assert.match(spoken.textContent, /Second claim/);
assert.doesNotMatch(evidence.textContent, /First claim|Prohibited|Within file/,
  'The late first response must not replace the current turn or reveal its cached verdict');
await resolveProcess(processRequests[1]);
assert.match(evidence.textContent, /Second claim/);
assert.match(evidence.textContent, /Within file/);
assert.notEqual(right.hidden, true, 'The final result remains visible after the clip ends');
findClass(root, 'clip-history').children[0].onclick();
seek(video, video.currentTime);
assert.match(spoken.textContent, /First claim/, 'History selection survives the media seek events');
assert.match(evidence.textContent, /Prohibited/);
seek(video, 0.5);
assert.equal(timecode.textContent, '00:00:00', 'The monitor readout follows the actual seek position');
assert.match(monitorState.textContent, /Paused/i, 'Seeking back from the end clears the ended playback state');
assert.match(spoken.textContent, /First claim/);
assert.doesNotMatch(evidence.textContent, /Second claim|Within file/);
seek(video, 4);
assert.match(evidence.textContent, /Within file/, 'Seeking must preserve confirmed results');
assert.equal(processRequests.length, 2, 'Seeking must not issue evaluation requests');
video.ended = true; video.onended();
const continueButton = findClass(root, 'clip-continue');
assert.match(continueButton.textContent, /Continue/);
continueButton.onclick(); assert.equal(ended, 1);
dispose(); assert.equal(video.paused, true);

const skippedRoot = new Node();
const skipDispose = await clipScene(skippedRoot, 'a1', { clips: { a1: { available: true } }, translations: {} }, () => {});
const skippedVideo = findTag(skippedRoot, 'video');
seek(skippedVideo, 3);
await skippedVideo.play();
skippedVideo.currentTime = 4; skippedVideo.ontimeupdate();
skippedVideo.ended = true; skippedVideo.paused = true; skippedVideo.onended();
assert.equal(processRequests.length, 2, 'A seek past the start must not evaluate an unheard line');
skipDispose();

const disposedRoot = new Node();
const disposePending = await clipScene(disposedRoot, 'a1', { clips: { a1: { available: true } }, translations: {} }, () => {});
const disposedVideo = findTag(disposedRoot, 'video');
await disposedVideo.play(); disposedVideo.currentTime = 2.1; disposedVideo.ontimeupdate();
assert.equal(processRequests.length, 3);
disposedVideo.currentTime = 4; disposedVideo.ontimeupdate();
disposePending();
const disposedContent = disposedRoot.textContent;
await resolveProcess(processRequests[2]);
assert.equal(disposedRoot.textContent, disposedContent, 'A response after disposal cannot modify the clip');
assert.equal(processRequests.length, 3);

loadedClip = { ...clip, turns: [clip.turns[0]], atoms: [clip.atoms[0]], verdicts: [clip.verdicts[0]] };
const retryRoot = new Node();
const retryDispose = await clipScene(retryRoot, 'a1', { clips: { a1: { available: true } }, translations: {} }, () => {});
const retryVideo = findTag(retryRoot, 'video');
await retryVideo.play(); retryVideo.currentTime = 2; retryVideo.ontimeupdate();
retryVideo.ended = true; retryVideo.paused = true; retryVideo.onended();
assert.equal(processRequests.length, 4);
await resolveProcess(processRequests[3], false);
assert.doesNotMatch(findClass(retryRoot, 'clip-evidence').textContent, /Prohibited|Within file/);
const retryButton = findButton(retryRoot, /retry/i);
assert.ok(retryButton, 'A failed evaluation offers an explicit retry');
retryButton.onclick();
assert.equal(processRequests.length, 5);
assert.deepEqual(processRequests[4].body, { turn_id: 't01', reprocess: true });
await resolveProcess(processRequests[4]);
assert.match(findClass(retryRoot, 'clip-evidence').textContent, /Prohibited/);
retryDispose();

loadedClip = { ...loadedClip, verdicts: [{ ...clip.verdicts[0], verdict: 'abstain', reason_code: 'LLM_OFF' }] };
const unavailableRoot = new Node();
const unavailableDispose = await clipScene(unavailableRoot, 'a1', { clips: { a1: { available: true } }, translations: {} }, () => {});
const unavailableVideo = findTag(unavailableRoot, 'video');
await unavailableVideo.play(); unavailableVideo.currentTime = 2; unavailableVideo.ontimeupdate();
await resolveProcess(processRequests[5]);
assert.match(findClass(unavailableRoot, 'clip-flow-status').textContent, /unavailable/i);
assert.doesNotMatch(findClass(unavailableRoot, 'clip-flow').textContent, /Needs human|Prohibited|Within file/,
  'An unavailable model is not presented as a completed human-review verdict');
assert.ok(findButton(unavailableRoot, /retry/i));
unavailableDispose(); loadedClip = clip;

deferProcessing = false;
const backupRoot = new Node();
await clipScene(backupRoot, 'a2', { clips: {}, translations: {}, video_mode: true }, () => {});
assert.match(backupRoot.textContent, /Clip shown live on the presenting laptop only/);
const sessionRoot = new Node();
const close = await sessionScreen(sessionRoot, 'kera-reenact-2025-01-08', {}, {});
assert.deepEqual(sent.at(-1), { kind: 'reenactment', sku_id: 'kera', as_of: '2025-01-08' });
close();
console.log('P4 recall, search, subtitles, clip timing and video-mode checks passed');
