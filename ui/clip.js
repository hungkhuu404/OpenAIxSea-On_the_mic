import { el } from './dom.js';
import { loadSession } from './data.js';
import { sessionView } from './session.js';

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

export async function clipScene(root, key, manifest, onEnd) {
  const clip = manifest.clips[key] || {};
  const layout = el('section', '', 'clip-scene');
  const left = el('section'), right = el('section');
  layout.append(left, right); root.append(layout);
  const session = await loadSession(`clip-${key}`);
  const history = (session.events || []).filter(row => row.type === 'step' && row.payload.session_id === session.session_id);
  const view = sessionView(session, manifest.translations);
  if (session.as_of_note) right.append(el('p', session.as_of_note, 'notice'));
  right.append(view.node);
  if (!clip.available) {
    left.append(el('p', manifest.video_mode ? 'Clip shown live on the presenting laptop only' : 'Clip unavailable', 'notice'));
    history.forEach(view.event);
    return () => {};
  }
  const video = el('video'); video.controls = true; video.src = `/present/clip/${key}.mp4`;
  left.append(video);
  for (const line of [session.speaker_line, session.source_line]) if (line) left.append(el('p', line));
  const caption = el('div', '', 'clip-subtitles'); left.append(caption);
  let subtitles = [];
  if (clip.subtitles) subtitles = await (await fetch(`/present/clip/${key}.subs.json`)).json();
  const timeline = session.turns.flatMap(turn => {
    const rows = history.filter(row => row.payload.turn_id === turn.turn_id);
    const total = rows.reduce((sum, row) => sum + row.payload.ms, 0) || 1;
    let elapsed = 0;
    return rows.map(row => {
      elapsed += row.payload.ms;
      return { time: turn.start_s + (turn.end_s - turn.start_s) * elapsed / total, row };
    });
  }).sort((a, b) => a.time - b.time);
  let previous = -1, cursor = 0;
  video.ontimeupdate = () => {
    const time = video.currentTime;
    if (time < previous) { cursor = 0; view.resetSteps(); }
    previous = time;
    const turn = session.turns.find(turn => turn.start_s <= time && turn.end_s > time);
    if (turn) view.selectTurn(turn.turn_id);
    while (cursor < timeline.length && timeline[cursor].time <= time) view.event(timeline[cursor++].row);
    const line = subtitles.find(line => line.start <= time && line.end > time);
    caption.replaceChildren(el('p', subtitleLines(line?.vi || ''), 'subtitle-vi'),
      el('p', subtitleLines(line?.en || ''), 'subtitle-en'));
  };
  video.onended = () => { layout.classList.add('clip-ended'); right.hidden = true; onEnd(); };
  return () => { video.pause(); video.ontimeupdate = null; video.onended = null; };
}
