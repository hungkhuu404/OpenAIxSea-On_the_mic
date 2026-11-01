import { el, quote } from './dom.js';
import { verdicts } from './strings.js';
import { post } from './data.js';

export function badge(value = 'abstain', flags = []) {
  const spec = verdicts[value] || verdicts.abstain;
  const node = el('span', '', `badge ${value}`);
  const shape = el('span', spec.symbol, `shape ${spec.shape}`);
  shape.setAttribute('aria-hidden', 'true');
  node.append(shape,
    el('span', spec.label + (value === 'abstain' && flags.includes('PREV_RED') ? ' · previously Prohibited' : '')));
  return node;
}

export function verdictCard(atom, verdict, session, translations = {}) {
  const card = el('article', '', `card claim-card ${verdict.verdict}`);
  card.setAttribute('aria-label', `Claim ${atom.atom_id}: ${(verdicts[verdict.verdict] || verdicts.abstain).label}`);
  if ((verdict.flags || []).includes('RECALLED_AFTER')) {
    card.classList.add('recalled');
    card.append(el('p', 'Recalled after broadcast · No human needed', 'notice'));
  }
  const head = el('div', '', 'card-head');
  head.append(badge(verdict.verdict, verdict.flags), el('span', atom.atom_id, 'mono claim-meta'));
  if (verdict.decided_by) head.append(el('span', `Decided by ${verdict.decided_by}`, 'decision-note'));
  const columns = el('div', '', 'columns');
  const turn = session.turns.find(row => row.turn_id === atom.turn_id);
  const speech = el('section', '', 'column');
  speech.append(el('h3', 'Speech', 'column-title'), el('p', turn?.display_speaker || '', 'muted'),
    quote(turn?.text || atom.span, atom.span, translations, 'speech'));
  const dossier = el('section', '', 'column');
  dossier.append(el('h3', 'Dossier', 'column-title'));
  const id = verdict.permitted_id || verdict.closest_permitted_id;
  const permitted = session.context?.P?.find(item => item.permitted_id === id);
  if (verdict.verdict === 'yellow') dossier.append(el('p', 'Dossier only permits:'));
  if (permitted) dossier.append(quote(permitted.text, verdict.evidence_span, translations));
  else dossier.append(el('p', 'No permitted item selected for this decision.', 'muted'));
  if (verdict.numbers) {
    const comparison = [];
    if (verdict.numbers.claim) comparison.push(`Claim ${verdict.numbers.claim}`);
    comparison.push(verdict.numbers.dossier ? `Dossier ${verdict.numbers.dossier}` : 'Dossier comparison not established');
    dossier.append(el('p', comparison.join(' · '), 'numbers'));
  }
  const law = el('section', '', 'column');
  law.append(el('h3', 'Law', 'column-title'));
  // Only display_text supplied by the server is eligible for rendering.
  for (const anchor of verdict.legal_basis || []) {
    law.append(el('p', anchor, 'mono'));
    const text = session.law_display?.[anchor];
    if (text) law.append(quote(text, verdict.law_span, translations));
    else law.append(el('p', 'Source text is unavailable.', 'muted'));
  }
  if (!verdict.legal_basis?.length) law.append(el('p', 'No legal basis attached to this decision.', 'muted'));
  columns.append(speech, dossier, law);
  const foot = el('div', '', 'card-foot');
  foot.append(el('span', [verdict.qlnd_category, verdict.action, verdict.reason_code].filter(Boolean).join(' · ')),
    el('span', session.source, 'muted source-note'));
  const report = el('button', 'Report wrong');
  report.disabled = Boolean(session.fixture);
  report.onclick = async () => {
    const reporter = window.prompt('Your GitHub handle');
    if (!reporter) return;
    try { await post('/api/report-wrong', { verdict_id: verdict.verdict_id, reporter }); report.textContent = 'Reported'; }
    catch { report.textContent = 'Report unavailable'; }
  };
  if (!session.public_mode && session.channel !== 'video') foot.append(report);
  card.append(head);
  if (verdict.verdict === 'abstain' && verdict.reason_code === 'LLM_NEI') {
    card.append(el('p', 'Human review is needed: the available dossier does not provide enough information to assess this claim.', 'notice'));
  }
  card.append(columns, foot);
  return card;
}
