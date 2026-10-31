import { el, quote } from './dom.js';
import { badge, verdictCard } from './verdict.js';
import { steps } from './strings.js';

export function sessionView(session, translations = {}, history = []) {
  const root = el('section', '', 'session');
  const turns = el('aside');
  const detail = el('section');
  let selected = session.turns[0]?.turn_id;
  const latestSteps = new Map();
  function recordStep(event) {
    if (event.type !== 'step' || event.payload.session_id !== session.session_id) return;
    const key = `${event.payload.turn_id}:${event.payload.step}`;
    const previous = latestSteps.get(key);
    if (previous?.id != null && event.id != null && Number(previous.id) >= Number(event.id)) return;
    latestSteps.set(key, event);
  }
  history.forEach(recordStep);
  const pipeline = el('section', '', 'pipeline');
  const flags = el('div', '', 'session-flags');
  const recall = el('div', '', 'recall-banner');
  const layout = el('div', '', 'session');
  function renderFlags() {
    flags.replaceChildren();
    for (const flag of session.flags || []) flags.append(el('p', flag.message, 'notice red'));
  }
  function selectTurn(id, scroll = false) {
    if (!session.turns.some(turn => turn.turn_id === id)) return;
    selected = id;
    for (const button of turns.children) {
      button.setAttribute('aria-pressed', String(button.dataset.turn === id));
      if (button.dataset.turn === id) {
        button.classList.add('search-hit');
        if (scroll) button.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
      } else button.classList.remove('search-hit');
    }
    renderDetail();
  }
  function renderDetail() {
    detail.replaceChildren();
    detail.append(el('h2', `Claims · ${selected || 'No turns'}`));
    for (const atom of session.atoms || []) {
      if (atom.turn_id !== selected) continue;
      const verdict = (session.verdicts || []).find(row => row.atom_key === `${session.session_id}:${atom.atom_id}`);
      if (verdict) detail.append(verdictCard(atom, verdict, session, translations));
    }
    pipeline.replaceChildren(el('h2', 'pipeline · this line'));
    for (const [code, name] of Object.entries(steps)) {
      const event = latestSteps.get(`${selected}:${code}`);
      const row = el('div', '', 'step');
      row.dataset.step = code;
      row.append(el('span', code, 'mono'), el('span', name), el('span', event?.payload.outcome || '—'), el('span', event ? `${event.payload.ms} ms` : '—', 'mono'));
      pipeline.append(row);
    }
    detail.append(pipeline);
  }
  function renderTurns() {
    turns.replaceChildren();
    for (const turn of session.turns) {
      const button = el('button', '', 'turn');
      button.dataset.turn = turn.turn_id;
      button.append(el('span', turn.start_s == null ? turn.turn_id : `${turn.start_s.toFixed(1)} s`, 'mono'), quote(turn.text, null, translations, 'speech'));
      if (turn.display_speaker) button.append(el('small', turn.display_speaker));
      const values = (session.verdicts || []).filter(row => row.atom_key.startsWith(`${session.session_id}:${turn.turn_id}.`));
      const value = ['red', 'yellow', 'abstain', 'green'].find(color => values.some(row => row.verdict === color));
      button.append(badge(value));
      button.onclick = () => selectTurn(turn.turn_id);
      button.setAttribute('aria-pressed', String(turn.turn_id === selected));
      turns.append(button);
    }
  }
  root.className = 'session-screen';
  layout.append(turns, detail); root.append(flags, recall, layout);
  renderFlags(); renderTurns(); renderDetail();
  return { node: root, selectTurn, resetSteps() { latestSteps.clear(); renderDetail(); }, update(updated) {
    session = updated;
    if (!session.turns.some(turn => turn.turn_id === selected)) selected = session.turns[0]?.turn_id;
    renderFlags(); renderTurns(); renderDetail();
  }, event(event) {
    if (event.type === 'recall.applied') {
      const affected = event.payload.sessions.find(row => row.session_id === session.session_id);
      if (affected) recall.textContent = `Recall applied · ${affected.flagged_atoms} flagged atoms · No human needed`;
    }
    if (event.type === 'step' && event.payload.session_id === session.session_id) {
      recordStep(event);
      renderDetail();
      const row = [...turns.children].find(item => item.dataset.turn === event.payload.turn_id);
      if (row) { row.classList.remove('running'); requestAnimationFrame(() => row.classList.add('running')); }
    }
  } };
}
