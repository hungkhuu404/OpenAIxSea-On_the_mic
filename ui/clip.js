import { el, quote } from './dom.js';
import { loadSession, post } from './data.js';
import { badge, verdictCard } from './verdict.js?v=serving-policy-1';
import { attachMonitorSurface } from './monitor-surface.js?v=signal-1';

export function subtitleLines(text, width = 42) {
  const lines = []; let line = '';
  for (const word of text.split(/\s+/)) {
    for (let offset = 0; offset < word.length; offset += width) {
      const part = word.slice(offset, offset + width);
      if (line && line.length + part.length + 1 > width) { lines.push(line); line = ''; }
      line += (line ? ' ' : '') + part;
    }
  }
  if (line) lines.push(line);
  return lines.join('\n');
}

const unavailableReasons = new Set(['LLM_OFF', 'LLM_TIMEOUT']);
function turnRows(session, id) {
  const atoms = (session.atoms || []).filter(atom => atom.turn_id === id);
  return atoms.map(atom => ({ atom, verdict: (session.verdicts || []).find(row =>
    row.atom_key === `${session.session_id}:${atom.atom_id}`) })).filter(row => row.verdict);
}
function resultColor(rows) {
  return ['red', 'yellow', 'abstain', 'green'].find(value => rows.some(row => row.verdict.verdict === value));
}

export async function clipScene(root, key, manifest, onEnd) {
  const clip = manifest.clips[key] || {};
  const layout = el('section', '', 'clip-scene');
  const left = el('section'), right = el('section');
  layout.append(left, right); root.append(layout);
  const session = await loadSession(`clip-${key}`);
  if (!session) throw new Error('Clip session unavailable');
  if (!clip.available) {
    left.append(el('p', manifest.video_mode ? 'Clip shown live on the presenting laptop only' : 'Clip unavailable', 'notice'));
    return () => {};
  }

  if (session.as_of_note) right.append(el('p', session.as_of_note, 'notice'));
  const flow = el('section', '', 'clip-flow');
  const heading = el('div', '', 'clip-flow-heading');
  heading.append(el('p', 'TRANSCRIPT / ASSESSMENT / EVIDENCE', 'eyebrow'), el('h2', 'Claim inspection'),
    el('p', 'Recorded transcript follows the clip. Each completed line is sent for evaluation.', 'muted'));
  const status = el('p', '', 'clip-flow-status'); status.setAttribute('role', 'status');
  const spoken = el('div', '', 'clip-spoken');
  const evidence = el('div', '', 'clip-evidence');
  const history = el('div', '', 'clip-history'); history.setAttribute('aria-label', 'Evaluated lines');
  flow.append(heading, status, spoken, evidence, history); right.append(flow);
  const video = el('video'); video.controls = true; video.src = `/present/clip/${key}.mp4`;
  video.playsInline = true; video.preload = 'metadata'; video.muted = false;
  video.setAttribute('aria-label', 'Recorded clip with synchronized claim evaluation');
  const monitor = el('section', '', 'monitor-shell');
  monitor.dataset.playback = 'ready';
  monitor.setAttribute('aria-label', 'Recorded clip inspection monitor');
  const toolbar = el('header', '', 'monitor-toolbar');
  const live = el('span', '', 'monitor-live');
  const dot = el('span', '', 'monitor-live-dot'); dot.setAttribute('aria-hidden', 'true');
  live.append(dot, el('span', 'LIVE REVIEW'));
  toolbar.append(el('span', `INPUT ${String(key).toUpperCase()}`, 'monitor-input'), live,
    el('span', 'RECORDED CLIP', 'monitor-recorded'));
  const viewport = el('div', '', 'monitor-viewport'); viewport.append(video);
  const readout = el('footer', '', 'monitor-readout');
  const timecode = el('time', '00:00:00', 'monitor-timecode');
  timecode.setAttribute('aria-label', 'Playback time'); timecode.setAttribute('datetime', 'PT0S');
  const monitorState = el('span', 'Ready', 'monitor-state');
  readout.append(timecode, monitorState); monitor.append(toolbar, viewport, readout); left.append(monitor);
  const detachMonitorSurface = attachMonitorSurface(monitor);
  const controls = el('div', '', 'clip-controls');
  const play = el('button', 'Play with sound'); play.type = 'button';
  const playback = el('p', 'Play the clip to begin.', 'muted'); playback.setAttribute('role', 'status');
  controls.append(play, playback); left.append(controls);
  for (const line of [session.speaker_line, session.source_line]) if (line) left.append(el('p', line));
  const caption = el('div', '', 'clip-subtitles'); caption.hidden = true; left.append(caption);
  const continueButton = el('button', 'Continue to reenactment', 'clip-continue');
  continueButton.type = 'button'; continueButton.hidden = true;
  continueButton.onclick = () => { continueButton.disabled = true; onEnd(); };
  left.append(continueButton);

  let subtitles = [];
  if (clip.subtitles) {
    try { subtitles = await (await fetch(`/present/clip/${key}.subs.json`)).json(); }
    catch { playback.textContent = 'Subtitles unavailable. The recorded transcript is still available.'; }
  }
  let disposed = false, selected = null, previousTime = Number(video.currentTime) || 0;
  let seeking = false, running = false, ended = false, evidenceKey = '', historyKey = '', spokenKey = '';
  const heard = new Set(), states = new Map(), queue = [];
  const activeTurn = time => session.turns.find(turn => turn.start_s <= time && turn.end_s > time);
  const pastTurn = time => session.turns.filter(turn => turn.end_s <= time).at(-1);

  function renderHistory() {
    const nextKey = `${selected}:${[...states].map(([id, state]) => `${id}:${state.phase}:${state.revision}`).join('|')}`;
    if (nextKey === historyKey) return;
    historyKey = nextKey;
    history.replaceChildren();
    for (const turn of session.turns) {
      const state = states.get(turn.turn_id);
      if (!state) continue;
      const button = el('button', `Line ${turn.turn_id} · ${state.phase === 'checking' || state.phase === 'queued' ? 'Checking…' :
        state.phase === 'error' || state.phase === 'unavailable' ? 'Unavailable' : 'Evaluated'}`);
      button.type = 'button'; button.setAttribute('aria-pressed', String(selected === turn.turn_id));
      if (state.phase === 'decided' && state.color) button.append(badge(state.color));
      button.onclick = () => {
        video.pause(); video.currentTime = Math.max(turn.start_s, turn.end_s - .001); selected = turn.turn_id;
        previousTime = video.currentTime; render();
      };
      history.append(button);
    }
  }
  function render() {
    if (disposed) return;
    const turn = session.turns.find(row => row.turn_id === selected);
    const state = turn && states.get(turn.turn_id);
    const phase = state?.phase || (turn ? 'listening' : 'waiting');
    flow.dataset.phase = phase;
    const statusText = phase === 'waiting' ? 'Ready · play the clip to begin.' :
      phase === 'listening' ? 'Following the spoken line · evaluation starts when the line ends.' :
      phase === 'queued' ? 'This line is queued for evaluation.' :
      phase === 'checking' ? 'Checking this line against the dossier and legal basis…' :
      phase === 'error' ? 'The evaluation request failed. Retry this line.' :
      phase === 'unavailable' ? 'AI checking is unavailable for this line. No final color is assigned.' :
      state.color === 'abstain' && state.rows.some(row => row.verdict.reason_code === 'LLM_NEI') ?
        'Evaluation complete · the available dossier is insufficient. Human review is needed.' :
      state.rows.length ? 'Assessment complete · review the supporting evidence below.' : 'Assessment complete · no claims identified.';
    if (status.textContent !== statusText) status.textContent = statusText;
    const currentCue = turn && subtitles.find(line =>
      line.start <= video.currentTime && line.end > video.currentTime && line.end > turn.start_s && line.start < turn.end_s);
    const text = turn ? (phase === 'listening' && currentCue?.vi ? currentCue.vi : turn.text) : '';
    spoken.className = `clip-spoken${phase === 'decided' && state.color ? ` ${state.color}` : ''}`;
    spoken.dataset.phase = phase;
    const nextSpokenKey = JSON.stringify([turn?.turn_id, text]);
    if (nextSpokenKey !== spokenKey) {
      spokenKey = nextSpokenKey;
      const body = turn ? quote(text, null, manifest.translations || {}, 'speech') :
        el('p', 'The spoken line will appear here as the clip plays.', 'muted');
      if (turn) body.classList.add('claim-reveal');
      spoken.replaceChildren(el('p', turn ? `RECORDED TRANSCRIPT · ${turn.turn_id}` : 'RECORDED TRANSCRIPT', 'eyebrow'), body);
    }
    const nextKey = `${selected}:${phase}:${state?.revision || 0}`;
    if (nextKey !== evidenceKey) {
      evidenceKey = nextKey; evidence.replaceChildren();
      if (phase === 'checking' || phase === 'queued') {
        evidence.append(el('div', 'Waiting for the actual assessment. The result will stay here after the video ends.', 'clip-checking'));
      } else if (phase === 'error' || phase === 'unavailable') {
        const message = el('p', phase === 'unavailable' ?
          'The checking service could not complete this line. This is not a human-review decision.' :
          'The service did not return an assessment. Your transcript has been kept.', 'notice');
        const retry = el('button', 'Retry'); retry.type = 'button';
        retry.onclick = () => enqueue(turn.turn_id, true);
        evidence.append(message, retry);
      }
      if (state?.session && ['decided', 'unavailable'].includes(phase)) {
        for (const row of state.rows) {
          if (!unavailableReasons.has(row.verdict.reason_code)) {
            const card = verdictCard(row.atom, row.verdict, state.session, manifest.translations || {});
            card.classList.add('evidence-reveal'); evidence.append(card);
          }
        }
      }
    }
    renderHistory();
  }
  async function drain() {
    if (running || disposed) return;
    running = true;
    while (queue.length && !disposed) {
      const id = queue.shift(), state = states.get(id);
      state.phase = 'checking'; render();
      try {
        await post(`/api/sessions/${encodeURIComponent(session.session_id)}/process`, { turn_id: id, reprocess: true });
        if (disposed) break;
        const updated = await loadSession(session.session_id);
        if (disposed) break;
        if (!updated) throw new Error('Session unavailable');
        state.session = updated; state.rows = turnRows(updated, id);
        const atoms = (updated.atoms || []).filter(atom => atom.turn_id === id);
        const missing = state.rows.length !== atoms.length;
        state.phase = missing || state.rows.some(row => unavailableReasons.has(row.verdict.reason_code)) ? 'unavailable' : 'decided';
        state.color = state.phase === 'decided' ? resultColor(state.rows) : undefined;
      } catch {
        if (!disposed) state.phase = 'error';
      }
      if (!disposed) { state.revision++; render(); }
    }
    running = false;
  }
  function enqueue(id, retry = false) {
    if (disposed) return;
    const existing = states.get(id);
    if (existing && (!retry || ['checking', 'queued'].includes(existing.phase))) return;
    states.set(id, { phase: 'queued', revision: (existing?.revision || 0) + 1, rows: [] });
    queue.push(id); render(); void drain();
  }
  function tick(finishing = false) {
    if (disposed) return;
    const time = Number(video.currentTime) || 0;
    const seconds = Math.floor(Math.max(0, time));
    timecode.textContent = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
      .map(value => String(value).padStart(2, '0')).join(':');
    timecode.setAttribute('datetime', `PT${seconds}S`);
    const advancing = !seeking && !video.seeking && time >= previousTime && (!video.paused || finishing);
    if (advancing) {
      for (const turn of session.turns) {
        if (previousTime <= turn.start_s && time >= turn.start_s) heard.add(turn.turn_id);
        if (heard.has(turn.turn_id) && time >= turn.end_s) enqueue(turn.turn_id);
      }
    }
    const turn = activeTurn(time) || pastTurn(time);
    selected = turn?.turn_id || null;
    previousTime = time;
    const line = subtitles.find(row => row.start <= time && row.end > time);
    caption.hidden = !line;
    caption.replaceChildren(el('p', subtitleLines(line?.vi || ''), 'subtitle-vi'),
      el('p', subtitleLines(line?.en || ''), 'subtitle-en'));
    render();
  }
  function playbackState() {
    const phase = ended ? 'ended' : !video.paused ? 'playing' :
      monitor.dataset.playback === 'ready' && !video.currentTime ? 'ready' : 'paused';
    monitor.dataset.playback = phase;
    monitorState.textContent = { ready: 'Ready', playing: 'Playing', paused: 'Paused', ended: 'Ended' }[phase];
    play.textContent = video.paused ? 'Play with sound' : 'Pause clip';
    playback.textContent = video.paused ? ended ? 'Clip finished. Evaluation results remain beside the video.' : 'Clip paused.' :
      video.muted || video.volume === 0 ? 'Playing · sound muted.' : 'Playing · sound on.';
  }
  play.onclick = async () => {
    if (!video.paused) { video.pause(); return; }
    video.muted = false; video.volume = 1;
    try { await video.play(); }
    catch { playback.textContent = 'Use the video Play control to start playback.'; }
  };
  video.onplay = () => {
    ended = false; continueButton.hidden = true;
    previousTime = Number(video.currentTime) || 0;
    const turn = activeTurn(previousTime);
    if (turn && previousTime <= turn.start_s + .25) heard.add(turn.turn_id);
    playbackState(); tick();
  };
  video.onpause = playbackState; video.onvolumechange = playbackState;
  video.ontimeupdate = () => tick();
  video.onseeking = () => {
    seeking = true; ended = false; heard.clear(); previousTime = Number(video.currentTime) || 0;
    playbackState();
  };
  video.onseeked = () => {
    seeking = false; previousTime = Number(video.currentTime) || 0;
    const turn = activeTurn(previousTime);
    if (!video.paused && turn && previousTime <= turn.start_s + .25) heard.add(turn.turn_id);
    tick();
  };
  video.onended = () => { ended = true; tick(true); continueButton.hidden = false; playbackState(); };
  render();
  return () => {
    disposed = true; queue.length = 0;
    detachMonitorSurface();
    video.onplay = null; video.onpause = null; video.onvolumechange = null;
    video.ontimeupdate = null; video.onended = null; video.onseeking = null; video.onseeked = null;
    video.pause(); playbackState();
  };
}
