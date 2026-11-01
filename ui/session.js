import { el, quote } from './dom.js';
import { badge, verdictCard } from './verdict.js';
import { steps, verdicts } from './strings.js';

// Historical decisions remain in the ledger; the UI displays each current
// atom's newest recorded decision without deriving new verdicts.
function currentVerdicts(session) {
  const atoms = new Set((session.atoms || []).map(atom => `${session.session_id}:${atom.atom_id}`));
  const latest = new Map();
  for (const row of session.verdicts || []) {
    if (!atoms.has(row.atom_key)) continue;
    const previous = latest.get(row.atom_key);
    if (previous?.verdict_id != null && row.verdict_id != null) {
      if (Number(previous.verdict_id) > Number(row.verdict_id)) continue;
    } else if (previous?.ts && row.ts && previous.ts > row.ts) continue;
    latest.set(row.atom_key, row);
  }
  return latest;
}

export function sessionView(session, translations = {}, history = []) {
  const root = el('section', '', 'session-screen');
  const overview = el('section', '', 'session-overview');
  overview.setAttribute('aria-label', 'Session context and decision totals');
  const transcript = el('aside', '', 'transcript-panel');
  transcript.setAttribute('aria-label', 'Session transcript');
  const transcriptHeading = el('div', '', 'panel-heading');
  const turns = el('div', '', 'turn-list');
  const detail = el('section', '', 'claim-detail');
  detail.setAttribute('aria-label', 'Selected line evidence');
  let selected = session.turns[0]?.turn_id;
  let decisions = currentVerdicts(session);
  const latestSteps = new Map();
  function recordStep(event) {
    if (event.type !== 'step' || event.payload.session_id !== session.session_id) return;
    const key = `${event.payload.turn_id}:${event.payload.step}`;
    const previous = latestSteps.get(key);
    if (previous?.id != null && event.id != null && Number(previous.id) >= Number(event.id)) return;
    latestSteps.set(key, event);
  }
  history.forEach(recordStep);
  const pipeline = el('details', '', 'pipeline');
  const pipelineToggle = el('summary', 'Processing trace', 'pipeline-toggle');
  const pipelineRows = el('div', '', 'pipeline-rows');
  pipeline.append(pipelineToggle, pipelineRows);
  const flags = el('div', '', 'session-flags');
  const recall = el('div', '', 'recall-banner');
  recall.setAttribute('role', 'status');
  const layout = el('div', '', 'session');
  function renderOverview() {
    const context = el('div', '', 'session-context');
    const heading = el('div', '', 'session-title');
    heading.append(el('span', session.fixture ? 'SAMPLE SESSION' : session.live ? 'LIVE SESSION' : 'SESSION CONTEXT', 'eyebrow'),
      el('h2', session.sku_pin || 'Product not pinned'));
    const meta = el('dl', '', 'session-meta');
    for (const [label, value] of [['Broadcast date', session.as_of || 'Not available'],
      ['Channel', ({ livestream: 'Livestream', written: 'Written', video: 'Video' })[session.channel] || 'Not available']]) {
      const item = el('div', '', 'meta-item');
      item.append(el('dt', label), el('dd', value)); meta.append(item);
    }
    context.append(heading, meta, el('p', session.source || 'Source not available', 'session-source'));
    const summary = el('div', '', 'verdict-summary');
    for (const value of ['red', 'yellow', 'abstain', 'green']) {
      const count = [...decisions.values()].filter(row => row.verdict === value).length;
      const stat = el('div', '', `summary-stat ${value}`);
      stat.dataset.verdict = value;
      stat.setAttribute('aria-label', `${count} ${verdicts[value].label}`);
      stat.append(el('strong', String(count), 'summary-count'), badge(value));
      summary.append(stat);
    }
    const coverage = el('p', `${decisions.size} of ${(session.atoms || []).length} claims have a recorded decision.`, 'summary-coverage');
    overview.replaceChildren(context, summary, coverage);
  }
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
        if (scroll) button.scrollIntoView({ block: 'nearest',
          behavior: globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')?.matches ? 'auto' : 'smooth' });
      } else button.classList.remove('search-hit');
    }
    renderDetail();
  }
  function renderPipeline() {
    pipelineRows.replaceChildren();
    for (const [code, name] of Object.entries(steps)) {
      const event = latestSteps.get(`${selected}:${code}`);
      const row = el('div', '', 'step');
      row.dataset.step = code;
      const elapsed = Number.isFinite(event?.payload.ms) ? `${Math.round(event.payload.ms * 10) / 10} ms` : '—';
      row.append(el('span', code, 'mono'), el('span', name), el('span', event?.payload.outcome || '—'), el('span', elapsed, 'mono'));
      pipelineRows.append(row);
    }
  }
  function renderDetail() {
    const atoms = (session.atoms || []).filter(atom => atom.turn_id === selected);
    const heading = el('div', '', 'detail-heading');
    heading.append(el('h2', `Claims · ${selected || 'No turns'}`),
      el('span', `${atoms.length} ${atoms.length === 1 ? 'claim' : 'claims'}`, 'claim-count'));
    detail.replaceChildren(heading);
    let rendered = 0;
    for (const atom of atoms) {
      const verdict = decisions.get(`${session.session_id}:${atom.atom_id}`);
      if (verdict) { detail.append(verdictCard(atom, verdict, session, translations)); rendered++; }
    }
    if (!rendered) {
      const empty = el('div', '', 'empty-state');
      empty.append(el('h3', atoms.length ? 'Awaiting a recorded decision' : selected ? 'No claims on this line' : 'No turns available'),
        el('p', atoms.length ? 'Evidence will appear when a decision is recorded for this line.' :
          selected ? 'Choose another transcript line to inspect its claims and evidence.' : 'The transcript will appear when a session is available.', 'muted'));
      detail.append(empty);
    } else if (rendered < atoms.length) {
      detail.append(el('p', `${atoms.length - rendered} ${atoms.length - rendered === 1 ? 'claim is' : 'claims are'} awaiting a recorded decision.`, 'muted'));
    }
    renderPipeline();
    detail.append(pipeline);
  }
  function renderTurns() {
    const focusedTurn = document.activeElement?.dataset?.turn;
    const buttons = [];
    transcriptHeading.replaceChildren(el('h2', 'Transcript'), el('span', `${session.turns.length} lines`, 'claim-count'));
    for (const turn of session.turns) {
      const button = el('button', '', 'turn');
      button.type = 'button'; button.dataset.turn = turn.turn_id;
      const meta = el('span', '', 'turn-meta');
      meta.append(el('span', turn.start_s == null ? turn.turn_id : `${turn.start_s.toFixed(1)} s`, 'mono turn-id'));
      if (turn.display_speaker) meta.append(el('span', turn.display_speaker, 'turn-speaker'));
      const values = [...decisions.values()].filter(row => row.atom_key.startsWith(`${session.session_id}:${turn.turn_id}.`));
      const value = ['red', 'yellow', 'abstain', 'green'].find(color => values.some(row => row.verdict === color));
      if (value) {
        button.className = `turn ${value}`;
        button.dataset.verdict = value;
      }
      button.append(meta, quote(turn.text, null, translations, 'speech'),
        value ? badge(value) : el('span', 'No recorded decision', 'turn-pending'));
      button.onclick = () => selectTurn(turn.turn_id);
      button.setAttribute('aria-pressed', String(turn.turn_id === selected));
      buttons.push(button);
    }
    turns.replaceChildren(...buttons);
    if (focusedTurn) buttons.find(button => button.dataset.turn === focusedTurn)?.focus({ preventScroll: true });
    if (!session.turns.length) turns.append(el('p', 'No transcript lines available.', 'empty-state muted'));
  }
  transcript.append(transcriptHeading, turns);
  layout.append(transcript, detail); root.append(overview, flags, recall, layout);
  renderOverview(); renderFlags(); renderTurns(); renderDetail();
  return { node: root, selectTurn, resetSteps() { latestSteps.clear(); renderPipeline(); }, update(updated) {
    session = updated;
    decisions = currentVerdicts(session);
    if (!session.turns.some(turn => turn.turn_id === selected)) selected = session.turns[0]?.turn_id;
    renderOverview(); renderFlags(); renderTurns(); renderDetail();
  }, event(event) {
    if (event.type === 'recall.applied') {
      const affected = event.payload.sessions.find(row => row.session_id === session.session_id);
      if (affected) recall.textContent = `Recall applied · ${affected.flagged_atoms} flagged atoms · No human needed`;
    }
    if (event.type === 'step' && event.payload.session_id === session.session_id) {
      recordStep(event);
      // Step telemetry should never replace the evidence or its focused controls.
      renderPipeline();
      const row = [...turns.children].find(item => item.dataset.turn === event.payload.turn_id);
      if (row) { row.classList.remove('running'); requestAnimationFrame(() => row.classList.add('running')); }
    }
  } };
}
