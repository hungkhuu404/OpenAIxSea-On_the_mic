import { el } from './dom.js';
import { request, post } from './data.js';

export async function sessionTools(session, view, navigate) {
  const root = el('section', '', 'session-tools');
  if (session.sku_pin === 'kera' && session.channel !== 'video') {
    const dates = el('div', '', 'date-bar');
    dates.setAttribute('role', 'group'); dates.setAttribute('aria-label', 'Compare broadcast dates and replay the recall');
    for (const [date, label] of [['2024-12-12', '12/12/2024'], ['2025-01-08', '08/01/2025']]) {
      const button = el('button', label); button.type = 'button';
      button.setAttribute('aria-pressed', String(session.as_of === date));
      button.onclick = () => navigate(`kera-reenact-${date}`);
      dates.append(button);
    }
    const { recall_date: date } = await request('/api/ui/kera');
    const formatted = date.split('-').reverse().join('/');
    const recall = el('button', `Recall notice arrives (replay of the ${formatted} decision)`);
    recall.type = 'button';
    recall.disabled = Boolean(session.public_mode);
    const recallStatus = el('p', session.public_mode ? 'Recall replay is unavailable in public mode.' : '', 'recall-status muted');
    recallStatus.setAttribute('role', 'status');
    recall.onclick = async () => {
      if (recall.disabled || session.public_mode) return;
      recall.disabled = true; recallStatus.textContent = 'Applying recall replay…';
      try {
        await post('/api/dossier-events/recall', { sku_id: 'kera', recall_date: date,
          source: `HCMC Food Safety Department decision, ${formatted} (replay)` });
        recallStatus.textContent = 'Recall replay applied.';
      } catch (error) { recallStatus.textContent = `Unable to replay the recall. ${error.message}`; }
      finally { recall.disabled = Boolean(session.public_mode); }
    };
    dates.append(recall); root.append(dates, recallStatus);
  }
  const form = el('form', '', 'search-form'); form.setAttribute('role', 'search');
  const label = el('label', 'Search this session');
  const input = el('input'); input.type = 'search'; input.placeholder = 'Word or phrase · diacritics optional';
  input.id = `session-search-${session.session_id}`; label.htmlFor = input.id;
  const search = el('button', 'Search'); search.type = 'submit'; search.disabled = true;
  const controls = el('div', '', 'search-controls'); controls.append(input, search);
  const help = el('p', 'Enter to search; arrow keys move between matches.', 'search-help muted');
  help.id = `${input.id}-help`; input.setAttribute('aria-describedby', help.id);
  const results = el('div', '', 'search-results'), status = el('p', '', 'search-status muted');
  status.setAttribute('role', 'status'); status.setAttribute('aria-atomic', 'true');
  form.append(label, controls, help, status, results);
  let hits = [], index = -1, revision = 0, searching = false;
  function clearResults() {
    hits = []; index = -1; results.replaceChildren();
  }
  function updateSearch() {
    search.disabled = searching || !input.value.trim();
    search.textContent = searching ? 'Searching…' : 'Search';
    results.setAttribute('aria-busy', String(searching));
  }
  input.oninput = () => {
    revision++; searching = false; clearResults(); status.textContent = ''; updateSearch();
  };
  function jump(next) {
    if (!hits.length) return;
    index = (next + hits.length) % hits.length;
    view.selectTurn(hits[index].turn_id, true);
    for (const [i, row] of [...results.children].entries()) row.setAttribute('aria-pressed', String(i === index));
  }
  async function runSearch(event) {
    event.preventDefault();
    const query = input.value.trim(), current = ++revision;
    clearResults();
    if (!query) {
      searching = false; status.textContent = 'Enter a word or phrase to search.'; updateSearch(); return;
    }
    searching = true; status.textContent = 'Searching this session…'; updateSearch();
    try {
      const rows = await request(`/api/search?q=${encodeURIComponent(query)}&session_id=${encodeURIComponent(session.session_id)}`);
      if (current !== revision || input.value.trim() !== query) return;
      hits = rows;
      hits.forEach((hit, i) => {
        const button = el('button', `${hit.display_speaker} · ${hit.snippet}`); button.type = 'button';
        button.onclick = () => jump(i); results.append(button);
      });
      status.textContent = hits.length ? `${hits.length} ${hits.length === 1 ? 'match' : 'matches'}. Select a result to inspect the turn.` : `No matches for “${query}”. Try another word or phrase.`;
      jump(0);
    } catch (error) {
      if (current !== revision || input.value.trim() !== query) return;
      status.textContent = `Unable to search this session. ${error.message}`;
    } finally {
      if (current === revision) { searching = false; updateSearch(); }
    }
  }
  form.onsubmit = runSearch;
  input.onkeydown = async event => {
    if (!['Enter', 'ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault();
    if (event.key === 'Enter') return runSearch(event);
    jump(index + (event.key === 'ArrowDown' ? 1 : -1));
  };
  root.append(form);
  return root;
}
