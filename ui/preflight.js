import { el } from './dom.js';
import { post } from './data.js';
import { badge } from './verdict.js';

export function sentenceBox() {
  const form = el('form', '', 'sentence-box');
  const sku = el('input'); sku.name = 'sku_id'; sku.value = 'dk_betics_gold'; sku.required = true;
  const date = el('input'); date.type = 'date'; date.name = 'as_of'; date.required = true;
  date.value = new Date().toISOString().slice(0, 10);
  const text = el('textarea'); text.name = 'text'; text.required = true;
  const output = el('div'); output.setAttribute('role', 'status');
  const submit = el('button', 'Check sentence'); submit.type = 'submit';
  for (const [title, control] of [['Product SKU', sku], ['Broadcast date', date], ['Sentence', text]]) {
    const label = el('label', title); label.append(control); form.append(label);
  }
  form.append(submit, output);
  form.onsubmit = async event => {
    event.preventDefault(); submit.disabled = true; output.replaceChildren(el('p', 'Checking…'));
    try {
      const verdict = await post('/api/preflight', { sku_id: sku.value, as_of: date.value, text: text.value });
      output.replaceChildren(badge(verdict.verdict),
        el('p', verdict.reason_code || verdict.decided_by || '', 'mono'));
    } catch (error) { output.replaceChildren(el('p', error.message, 'notice')); }
    finally { submit.disabled = false; }
  };
  return form;
}
