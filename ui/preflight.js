import { el } from './dom.js';
import { post } from './data.js';
import { badge } from './verdict.js';

export function sentenceBox() {
  const form = el('form', '', 'sentence-box');
  form.append(el('p', 'Check the original wording against a product dossier at the broadcast date.', 'sentence-intro muted'));
  const sku = el('input'); sku.name = 'sku_id'; sku.value = 'dk_betics_gold'; sku.required = true;
  const date = el('input'); date.type = 'date'; date.name = 'as_of'; date.required = true;
  date.value = new Date().toISOString().slice(0, 10);
  const text = el('textarea'); text.name = 'text'; text.required = true;
  text.rows = 3; text.placeholder = 'Paste or type the sentence to check';
  const output = el('div', '', 'sentence-result');
  output.setAttribute('role', 'status'); output.setAttribute('aria-atomic', 'true');
  const submit = el('button', 'Check sentence'); submit.type = 'submit';
  const metadata = el('div', '', 'sentence-metadata');
  form.append(metadata);
  for (const [title, control, hint, parent] of [
    ['Product SKU', sku, 'Use the product identifier from the session.', metadata],
    ['Broadcast date', date, 'The dossier is checked as it stood on this date.', metadata],
    ['Sentence', text, 'Keep the original wording and diacritics. All fields are required.', form],
  ]) {
    const field = el('div', '', 'form-field');
    const label = el('label', title); label.append(control);
    const help = el('p', hint, 'field-help muted'); help.id = `sentence-${control.name}-help`;
    control.setAttribute('aria-describedby', help.id);
    field.append(label, help); parent.append(field);
  }
  const actions = el('div', '', 'form-actions');
  const hint = el('p', '', 'field-help muted'); hint.id = 'sentence-submit-help';
  submit.setAttribute('aria-describedby', hint.id);
  actions.append(submit, hint); form.append(actions, output);
  let checking = false;
  function updateState() {
    const ready = Boolean(sku.value.trim() && date.value && text.value.trim());
    submit.disabled = checking || !ready;
    submit.textContent = checking ? 'Checking sentence…' : 'Check sentence';
    hint.textContent = checking ? 'Please wait for the initial result.' : ready ? '' : 'Complete all fields to enable this check.';
    for (const control of [sku, date, text]) control.disabled = checking;
    form.setAttribute('aria-busy', String(checking));
  }
  for (const control of [sku, date, text]) control.oninput = () => {
    output.replaceChildren(); updateState();
  };
  updateState();
  form.onsubmit = async event => {
    event.preventDefault();
    if (submit.disabled) return;
    checking = true; updateState(); output.replaceChildren(el('p', 'Checking sentence…'));
    try {
      const verdict = await post('/api/preflight', { sku_id: sku.value, as_of: date.value, text: text.value });
      output.replaceChildren(el('p', 'Initial check complete.'), badge(verdict.verdict));
      if (verdict.reason_code || verdict.decided_by) output.append(el('p', verdict.reason_code ?
        `Reason: ${verdict.reason_code}` : `Decided by: ${verdict.decided_by}`, 'mono muted'));
    } catch (error) { output.replaceChildren(el('p', `Unable to check this sentence. ${error.message}`, 'notice')); }
    finally { checking = false; updateState(); }
  };
  return form;
}
