import { el } from './dom.js';
import { request, post } from './data.js';

export async function sessionTools(session, view, navigate) {
  const root = el('section', '', 'session-tools');
  if (session.sku_pin === 'kera' && session.channel !== 'video') {
    const dates = el('div', '', 'date-bar');
    for (const [date, label] of [['2024-12-12', '12/12/2024'], ['2025-01-08', '08/01/2025']]) {
      const button = el('button', label);
      button.setAttribute('aria-pressed', String(session.as_of === date));
      button.onclick = () => navigate(`kera-reenact-${date}`);
      dates.append(button);
    }
    const { recall_date: date } = await request('/api/ui/kera');
    const formatted = date.split('-').reverse().join('/');
    const recall = el('button', `Recall notice arrives (replay of the ${formatted} decision)`);
    recall.disabled = Boolean(session.public_mode);
    recall.onclick = async () => {
      recall.disabled = true;
      try {
        await post('/api/dossier-events/recall', { sku_id: 'kera', recall_date: date,
          source: `HCMC Food Safety Department decision, ${formatted} (replay)` });
      } catch (error) { status.textContent = error.message; }
      finally { recall.disabled = Boolean(session.public_mode); }
    };
    dates.append(recall); root.append(dates);
  }
  const label = el('label', 'Search this session');
  const input = el('input'); input.type = 'search'; input.placeholder = 'Search · diacritics optional';
  label.append(input);
  const results = el('div', '', 'search-results'), status = el('p', '', 'muted');
  let hits = [], index = -1;
  function jump(next) {
    if (!hits.length) return;
    index = (next + hits.length) % hits.length;
    view.selectTurn(hits[index].turn_id, true);
    for (const [i, row] of [...results.children].entries()) row.setAttribute('aria-pressed', String(i === index));
  }
  input.onkeydown = async event => {
    if (!['Enter', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Enter') {
      const query = input.value.trim();
      if (!query) { hits = []; results.replaceChildren(); return; }
      try {
        const rows = await request(`/api/search?q=${encodeURIComponent(query)}&session_id=${encodeURIComponent(session.session_id)}`);
        if (input.value.trim() !== query) return;
        hits = rows; index = -1; results.replaceChildren();
        hits.forEach((hit, i) => {
          const button = el('button', `${hit.display_speaker} · ${hit.snippet}`);
          button.onclick = () => jump(i); results.append(button);
        });
        status.textContent = `${hits.length} matches`;
        jump(0);
      } catch (error) { status.textContent = error.message; }
    } else jump(index + (event.key === 'ArrowDown' ? 1 : -1));
  };
  root.append(label, status, results);
  return root;
}
