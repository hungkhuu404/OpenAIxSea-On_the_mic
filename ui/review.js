import { el, quote } from './dom.js';
import { request, post } from './data.js';
import { badge } from './verdict.js';

export const clock = value => value ? new Date(value).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : 'time unavailable';
export const draftTitle = status => ({ failed: 'Draft failed', error: 'Draft error', queued: 'Drafting', running: 'Drafting', passed: 'Draft passed' }[status] || 'No draft run');

export function queueGroups(items) {
  const keys = item => [...new Set(item.atom.near_hits.map(h => `${item.cluster_key}:${h.law_anchor}:${h.pattern}`))].sort();
  const buckets = new Map();
  for (const item of items) for (const key of keys(item)) {
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(item);
  }
  const used = new Set(), groups = [];
  for (const item of items) {
    if (used.has(item.atom_key)) continue;
    const group = keys(item).map(key => buckets.get(key).filter(member => !used.has(member.atom_key))).find(members => members.length >= 3) || [item];
    groups.push(group);
    for (const member of group) used.add(member.atom_key);
  }
  return groups;
}

function button(text, action) {
  const node = el('button', text); node.type = 'button'; node.onclick = action; return node;
}
function input(label, value = '') {
  const node = el('label', label), field = el('input'); field.value = value; node.append(field); return { node, field };
}
function attemptRows(attempts) {
  const list = el('section', '', 'attempts');
  for (const attempt of attempts) {
    const row = el('div', '', 'attempt');
    row.append(el('p', `${attempt.gate_result === 'pass' ? '✓' : '✕'} ${attempt.gate_result || 'In progress'} · ran at ${clock(attempt.started || attempt.ts)}`));
    if (attempt.job_id) row.append(el('small', `Job ${attempt.job_id}`));
    if (!attempt.started && !attempt.ts && attempt.run_started) row.append(el('small', `Run started ${clock(attempt.run_started)} · attempt time unavailable`));
    for (const error of attempt.error_lines || []) row.append(el('pre', error, 'mono'));
    if (attempt.files_read?.length) row.append(el('p', 'Files Codex read'), el('pre', attempt.files_read.join('\n'), 'mono'));
    list.append(row);
  }
  return list;
}
function funnelView(funnel, ts) {
  const node = el('section', '', 'review-funnel');
  const { K, I, J, R } = funnel;
  for (const [label, value] of [['runs', K], ['passed first try', I], ['passed after retry', J], ['failed', R]]) {
    node.append(el('div', `${label} · ${value}/${K} · ${clock(ts)}`));
  }
  return node;
}

export function whoRow(data) {
  const root = el('section', '', 'who-decided');
  function row(title, counts, standin = false) {
    if (!counts) return;
    const line = el('section'); line.append(el('h3', title));
    const measured = counts.source === 'ledger' ? `live, this session · ${clock(counts.measured_at)}` :
      `measured ${clock(counts.measured_at)} on ${standin ? 'a stand-in split of golden' : 'the sealed set'}`;
    const tiles = el('div', '', 'who-tiles');
    for (const [label, key] of [['code', 'code'], ['two votes', 'llm_votes'], ['human', 'human']]) {
      tiles.append(el('div', `${label} · ${counts[key]}/${counts.denominator} · ${measured}`));
    }
    line.append(tiles); root.append(line);
  }
  row('Live decisions', data.live);
  if (data.oracle) {
    row('v1', data.oracle.who_decided_v1, data.oracle.standin);
    row('v2', data.oracle.who_decided_v2, data.oracle.standin);
  } else root.append(el('p', 'No oracle measurement available.', 'muted'));
  return root;
}

function runSummary(card, run) {
  if (!run) return;
  card.append(el('h3', draftTitle(run.status || run.result)), el('p', `Job ${run.job_id} · Codex run id: unavailable in stored record`, 'mono'));
  if (run.dry) card.append(el('strong', 'DRY RUN', 'dry-run'));
  if (run.dry === undefined) card.append(el('p', 'Run mode unavailable in portable history', 'muted'));
  if (run.config) card.append(el('p', `Configuration ${run.config}`, 'mono'));
  if (run.pr_url) {
    const link = el('a', 'Open PR'); link.href = run.pr_url; card.append(link);
  }
}

export async function provenanceCard(ruleId, { approver = '', publicMode = false, refresh = async () => {}, job = null } = {}) {
  const card = el('article', '', 'card provenance'); card.dataset.rule = ruleId;
  const feedback = el('p', '', 'notice'); feedback.hidden = true;
  try {
    const rule = await request(`/api/rules/${encodeURIComponent(ruleId)}`);
    const run = job || rule.jobs[0] || rule.runs.at(-1);
    const state = run?.status || run?.result;
    card.append(el('h2', ruleId), el('p', `${rule.meta.origin} · approved by ${rule.meta.approved_by || 'Pending human approval'}`));
    runSummary(card, run);
    for (const source of rule.sources) {
      card.append(el('p', source.text, 'speech'), el('p', `${source.source_note || source.item_id} · ${clock(source.ts)}`, 'muted'));
      if (/constructed sentence/i.test(source.source_note || '')) card.append(el('span', 'Constructed sentence', 'chip'));
    }
    for (const law of rule.law) card.append(el('h3', law.anchor), quote(law.text, law.law_span, {}));
    const attempts = rule.attempts;
    card.append(attemptRows(attempts));
    if (rule.meta.origin === 't5') card.append(funnelView(rule.funnel, run?.ts || run?.started || rule.meta.approved_at));
    const impact = el('section'); card.append(impact);
    const approve = input('Approved by (GitHub handle)', approver);
    const merge = button('Merge', async () => {
      if (!approve.field.value.trim()) { feedback.hidden = false; feedback.textContent = 'approved_by is required'; approve.field.focus(); return; }
      merge.disabled = true;
      try {
        await post(`/api/rules/${encodeURIComponent(ruleId)}/merge`, { approved_by: approve.field.value.trim() });
        feedback.hidden = false; feedback.textContent = 'Merged. Decision counts refreshed.'; await refresh();
      } catch (error) { feedback.hidden = false; feedback.textContent = error.message; merge.disabled = false; }
    });
    merge.dataset.merge = ruleId;
    merge.disabled = publicMode || state !== 'passed';
    card.append(approve.node, merge, feedback);
    if (rule.meta.origin === 't5') {
      try {
        const dry = await request(`/api/rules/${encodeURIComponent(ruleId)}/dryrun`);
        const scope = dry.scope;
        impact.append(el('p', `If merged: ${dry.changed_atoms} old verdicts will change (cases ${dry.changed_cases}, claims ${dry.changed_atoms}) · cases ${dry.changed_cases}/${scope.cases}, claims ${dry.changed_atoms}/${scope.claims} · checked ${clock(scope.measured_at)}`));
        impact.append(el('p', `changes ${dry.changed_golden_outside_sources} golden cases outside the sources · ${dry.changed_golden_outside_sources}/${scope.golden_cases} golden cases · checked ${clock(scope.measured_at)}`));
        if (dry.flag_many) impact.append(el('span', 'Warning: changes many cases or another SKU', 'chip warning'));
      } catch (error) { impact.append(el('p', error.message, 'notice')); merge.disabled = true; }
    }
  } catch (error) {
    card.append(el('h2', ruleId)); runSummary(card, job);
    if (job) {
      for (const source of job.sources || []) card.append(el('p', source.text, 'speech'), el('p', source.source_note || source.item_id, 'muted'));
      card.append(attemptRows(job.attempts || []));
      if (job.funnel) card.append(funnelView(job.funnel, job.ts || job.started));
    }
    card.append(el('p', error.message, 'notice'));
  }
  return card;
}

export async function reviewView({ approver = '', publicMode = false, focusPR2 = false, target = '' } = {}) {
  const root = el('section', '', 'review');
  const labelPanel = el('section'), pendingPanel = el('section');
  const queue = el('aside', '', 'review-queue'), labels = el('section', '', 'review-labels'), cards = el('section', '', 'review-cards');
  const bottom = el('footer', '', 'review-bottom'); root.append(queue, labels, cards, bottom);
  labels.append(labelPanel, pendingPanel);
  let rules = [];
  async function refreshCounts() {
    try { bottom.replaceChildren(whoRow(await request('/api/who-decided'))); }
    catch (error) { bottom.replaceChildren(el('p', error.message, 'notice')); }
  }
  async function select(item) {
    labelPanel.replaceChildren(el('h2', 'Confirm a human label'), badge(item.verdict.verdict), el('p', item.atom.span, 'speech'), el('p', item.source));
    const colour = el('select'); colour.setAttribute('aria-label', 'Verdict colour');
    for (const value of ['red', 'yellow', 'green', 'abstain']) { const option = el('option', value); option.value = value; colour.append(option); }
    colour.value = item.verdict.verdict;
    const picker = el('select'); picker.setAttribute('aria-label', 'Rule');
    const none = el('option', 'No rule'); none.value = ''; picker.append(none);
    for (const rule of rules) { const option = el('option', `${rule.rule_id} · ${rule.meta.anchors.join(', ')}`); option.value = rule.rule_id; picker.append(option); }
    if (item.atom.near_hits.some(hit => hit.law_anchor === 'LDUOC-2016/D6/K15')) {
      picker.value = 'R04'; labelPanel.append(el('p', 'Near-hit suggestion: R04. Confirm the label and rule.', 'muted'));
    }
    picker.onchange = async () => { if (picker.value) cards.prepend(await provenanceCard(picker.value, { approver, publicMode, refresh: refreshCounts })); };
    const labeler = input('Labeler (GitHub handle)');
    const record = el('input'); record.type = 'checkbox';
    const recordLabel = el('label', 'record only (no trigger)'); recordLabel.prepend(record);
    const message = el('p');
    const confirm = button('Confirm label', async () => {
      if (!labeler.field.value.trim()) { message.textContent = 'Labeler is required'; return; }
      confirm.disabled = true;
      try {
        await post('/api/labels', { target: item.atom_key, label: colour.value, rule_id: picker.value || null, labeler: labeler.field.value.trim(), auto_trigger: !record.checked });
        labelPanel.replaceChildren(el('p', 'Label recorded.')); await refresh();
      } catch (error) { message.textContent = error.message; confirm.disabled = false; }
    });
    confirm.disabled = publicMode;
    labelPanel.append(colour, picker, labeler.node, recordLabel, confirm, message);
  }
  async function refreshQueue() {
    try {
      const items = await request('/api/queue'); queue.replaceChildren(el('h2', 'Needs human review'));
      for (const group of queueGroups(items)) {
        const wrapper = el('section', '', 'queue-group');
        if (group.length >= 3) wrapper.append(el('h3', `Cluster · ${group.length}/${items.length} queue items · ${clock(group[0].verdict.ts)}`));
        for (const item of group) {
          const choose = button(item.atom.span, () => select(item)); choose.className = 'queue-item';
          choose.append(el('small', `${item.score}/${item.score_max} risk points · ${clock(item.verdict.ts)}`, 'muted'));
          for (const chip of item.chips) choose.append(el('span', `${chip.text} +${chip.score}`, 'chip'));
          if (item.draft_state) choose.append(el('span', item.draft_state, 'chip'));
          choose.append(el('small', item.source), el('p', item.source_line)); wrapper.append(choose);
        }
        queue.append(wrapper);
      }
      if (!items.length) queue.append(el('p', 'No unlabelled abstain items.'));
    } catch (error) { queue.replaceChildren(el('p', error.message, 'notice')); }
  }
  async function refreshCards() {
    try {
      const [jobs, stage] = await Promise.all([request('/api/jobs'), request(`/api/review-stage?target=${encodeURIComponent(target)}`)]);
      const next = [];
      const pr1 = jobs.find(job => job.job_id === stage.pr1_job_id) || jobs.find(job => job.cluster_item_ids.includes(target)) || (!target ? jobs[0] : null);
      if (pr1) next.push(await provenanceCard(pr1.rule_id, { approver, publicMode, refresh: refreshCounts, job: pr1 }));
      const run = stage.pr2?.runs.toSorted((a, b) => new Date(a.started) - new Date(b.started)).at(-1);
      if (run) {
        const local = jobs.find(job => job.job_id === run.job_id);
        const pr2 = await provenanceCard(run.rule_id, { approver, publicMode, refresh: refreshCounts, job: local || { ...run, sources: stage.pr2.sources, funnel: stage.pr2.funnel } });
        pr2.dataset.pr = 'pr2'; next.push(pr2);
      } else next.push(el('p', 'PR2 has no stored run.', 'notice'));
      cards.replaceChildren(...next);
    } catch (error) { cards.replaceChildren(el('p', error.message, 'notice')); }
  }
  async function refreshPending() {
    try {
      const panels = [];
      for (const item of await request('/api/pending')) {
        const panel = el('section', '', 'card'); panel.append(el('h3', 'Pending dossier change'), el('pre', JSON.stringify(item, null, 2), 'mono'));
        const owner = input('Approved by (GitHub handle)', approver), message = el('p');
        const approve = button('Approve', async () => {
          if (!owner.field.value.trim()) { message.textContent = 'approved_by is required'; return; }
          approve.disabled = true;
          try { await post(`/api/pending/${encodeURIComponent(item.pending_id)}/approve`, { approved_by: owner.field.value.trim() }); await refresh(); }
          catch (error) { message.textContent = error.message; approve.disabled = false; }
        });
        approve.disabled = publicMode; panel.append(owner.node, approve, message); panels.push(panel);
      }
      pendingPanel.replaceChildren(...panels);
    } catch (error) { pendingPanel.replaceChildren(el('p', error.message, 'notice')); }
  }
  async function refresh() {
    await Promise.all([refreshQueue(), refreshCounts(), refreshCards(), refreshPending()]);
  }
  try {
    rules = await request('/api/rules');
    const browse = el('select'); browse.setAttribute('aria-label', 'Rule provenance'); browse.append(el('option', 'Inspect a rule'));
    for (const rule of rules) { const option = el('option', rule.rule_id); option.value = rule.rule_id; browse.append(option); }
    browse.onchange = async () => { if (rules.some(r => r.rule_id === browse.value)) cards.prepend(await provenanceCard(browse.value, { approver, publicMode, refresh: refreshCounts })); };
    labels.prepend(browse);
    labelPanel.append(el('h2', 'Select a claim to label'));
  } catch (error) { labelPanel.append(el('p', error.message, 'notice')); }
  await refresh();
  if (focusPR2) setTimeout(() => cards.querySelector('[data-pr="pr2"] [data-merge]')?.focus(), 0);
  return { node: root, refresh };
}
