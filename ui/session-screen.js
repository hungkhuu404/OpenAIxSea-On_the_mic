import { el } from './dom.js';
import { loadSession } from './data.js';
import { sessionView } from './session.js';
import { sessionTools } from './session-tools.js';
import { watchSession } from './session-events.js';

export async function sessionScreen(root, id, translations = {}, { publicMode = false, focusRecall = false, initialTurn } = {}) {
  const container = el('section'); root.append(container);
  let stop = () => {}, disposed = false, revision = 0;
  async function show(next) {
    const current = ++revision;
    const session = await loadSession(next, publicMode);
    if (disposed || current !== revision) return;
    if (!session) {
      container.replaceChildren(el('p', 'Replay not ready. Prepared sessions will appear when the replay modules are available.', 'notice'));
      return;
    }
    const view = sessionView(session, translations, session.events || []);
    const tools = await sessionTools(session, view, target => show(target).catch(error => {
      tools.append(el('p', error.message, 'notice'));
    }));
    if (disposed || current !== revision) return;
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
