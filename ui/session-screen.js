import { el } from './dom.js';
import { loadSession, post } from './data.js';
import { sessionView } from './session.js?v=turn-fills-1';
import { sessionTools } from './session-tools.js';
import { watchSession } from './session-events.js';

export async function sessionScreen(root, id, translations = {}, { publicMode = false, focusRecall = false, initialTurn, onNavigate } = {}) {
  const container = el('section'); root.append(container);
  let stop = () => {}, disposed = false, revision = 0;
  // A route may detach this screen before its first load returns a disposer.
  const active = current => !disposed && current === revision && container.isConnected !== false;
  async function show(next) {
    const current = ++revision;
    if (!active(current)) return;
    if (!publicMode && /^kera-reenact-(2024-12-12|2025-01-08)$/.test(next)) {
      await post('/api/sessions', { kind: 'reenactment', sku_id: 'kera', as_of: next.slice('kera-reenact-'.length) });
      if (!active(current)) return;
    }
    const session = await loadSession(next, publicMode);
    if (!active(current)) return;
    if (!session) {
      stop(); stop = () => {};
      container.replaceChildren(el('p', 'Replay not ready. Prepared sessions will appear when the replay modules are available.', 'notice'));
      return;
    }
    const view = sessionView(session, translations, session.events || []);
    const tools = await sessionTools(session, view, onNavigate ?? (target => show(target).catch(error => {
      tools.append(el('p', error.message, 'notice'));
    })));
    if (!active(current)) return;
    stop(); container.replaceChildren(tools, view.node);
    if (initialTurn) view.selectTurn(initialTurn);
    const latest = session.verdicts?.map(row => row.ts).sort().at(-1);
    document.querySelector('#time-context').textContent = session.live ? 'LIVE' : `REPLAY · processed ${latest ? new Date(latest).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '—'}`;
    if (!session.fixture) stop = watchSession(view, session.session_id, {
      publicMode, active: () => view.node.isConnected && !disposed,
    });
    if (focusRecall) tools.querySelector('.date-bar button:last-child')?.focus();
  }
  await show(id);
  return () => { disposed = true; revision++; stop(); };
}
