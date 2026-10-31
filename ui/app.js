import { el } from './dom.js';
import { request, setPresenter, events } from './data.js';
import { sessionScreen } from './session-screen.js';
import { clipScene } from './clip.js';
import { presenterView } from './presenter.js';
import { sentenceBox } from './preflight.js';
import { reviewView } from './review.js';

const app = document.querySelector('#app');
const html = document.documentElement;
let health = {}, presenter, stopEvents = () => {}, generation = 0;
const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* Storage is optional. */ } },
};
async function corner() {
  const node = document.querySelector('#stage-corner');
  node.hidden = html.dataset.stage !== 'true';
  if (node.hidden) return;
  try {
    health = await request('/api/health');
    node.textContent = `${health.backend} · offline=${health.offline}${health.loop_dry ? ' · DRY RUN' : ''}`;
  } catch { node.textContent = 'Health unavailable'; }
}
function palette() {
  html.dataset.palette = html.dataset.palette === 'oil' ? 'graphite' : 'oil';
  storage.set('palette', html.dataset.palette);
}
function stage() {
  html.dataset.stage = String(html.dataset.stage !== 'true'); storage.set('stage', html.dataset.stage); corner();
}
document.querySelector('#palette').onclick = palette;
document.querySelector('#stage').onclick = stage;
setInterval(corner, 10000);

async function showSession(root, id, translations = {}, options = {}) {
  const current = generation;
  const stop = await sessionScreen(root, id, translations, { publicMode: Boolean(health.public), ...options });
  if (current !== generation || !root.isConnected) stop();
  else stopEvents = stop;
}
async function scene(root, number, backup, manifest) {
  const content = el('section'); root.append(content); root = content;
  try {
    if (number === 1) {
      const key = backup ? 'a2' : 'a1';
      const stop = await clipScene(root, key, manifest, async () => {
        const reenactment = el('section'); root.append(reenactment);
        try { await showSession(reenactment, 'kera-reenact-2025-01-08', manifest.translations); }
        catch { reenactment.append(el('p', 'Reenactment not ready', 'notice')); }
      });
      if (!root.isConnected) stop();
      else stopEvents = stop;
    } else if (number <= 4) {
      await showSession(root, number === 3 ? 'kera-reenact-2024-12-12' : 'kera-reenact-2025-01-08', manifest.translations, { focusRecall: number === 4, initialTurn: number === 2 ? 't07' : undefined });
    } else {
      const view = await reviewView({ approver: manifest.present.approver, target: manifest.present.pr1_third_target, focusPR2: number === 6 });
      root.append(view.node);
      stopEvents = events(false, event => { if (['queue.changed', 'job.started', 'job.finished', 'job.attempt', 'rule.merged', 'backfill.done', 'dossier.pending'].includes(event.type)) view.refresh(); });
    }
  } catch { root.append(el('p', 'This scene is unavailable until its session is prepared.', 'notice')); }
}

async function route() {
  const current = ++generation;
  stopEvents(); stopEvents = () => {}; presenter = null;
  app.replaceChildren();
  try {
    health = await request('/api/health');
    const publicMode = Boolean(health.public);
    const isPresenter = !publicMode && location.hash.startsWith('#/present');
    setPresenter(isPresenter);
    document.querySelector('#present-link').hidden = publicMode;
    document.querySelector('#public-banner').hidden = !publicMode;
    html.dataset.palette = storage.get('palette') || health.palette || 'oil';
    html.dataset.stage = storage.get('stage') || String(Boolean(health.stage));
    await corner();
    if (current !== generation) return;
    if (isPresenter) {
      presenter = await presenterView((...args) => { stopEvents(); scene(...args); });
      if (current === generation) app.append(presenter.node);
    } else if (location.hash.startsWith('#/review')) {
      let present = {};
      if (!publicMode) {
        try { present = (await request('/api/present/manifest')).present; } catch { /* Optional presenter defaults. */ }
      }
      const view = await reviewView({ publicMode, approver: present.approver, target: present.pr1_third_target });
      if (current === generation) app.append(view.node);
      stopEvents = events(publicMode, event => {
        if (['queue.changed', 'job.started', 'job.finished', 'job.attempt', 'rule.merged', 'backfill.done', 'dossier.pending'].includes(event.type)) view.refresh();
      });
    } else {
      app.append(el('h1', 'Session ledger'));
      const id = new URLSearchParams(location.hash.split('?')[1]).get('session');
      app.append(sentenceBox());
      if (publicMode) {
        const sessions = await request('/api/sessions');
        if (sessions.length) {
          const label = el('label', 'Reenacted session');
          const select = el('select');
          for (const row of sessions) {
            const option = el('option', `${row.as_of} · ${row.session_id}`);
            option.value = row.session_id; select.append(option);
          }
          select.value = id || sessions[0].session_id;
          select.onchange = () => { location.hash = `#/?session=${encodeURIComponent(select.value)}`; };
          label.append(select); app.append(label);
        }
      }
      let translations = {};
      if (!publicMode) {
        try { translations = (await request('/api/present/manifest')).translations; } catch { /* Optional local translations. */ }
      }
      await showSession(app, id, translations);
    }
  } catch { app.append(el('p', 'Session unavailable. Please check the local service.', 'notice')); }
}
window.addEventListener('hashchange', route);
window.addEventListener('keydown', event => {
  if (event.target.closest('input, textarea, select, [contenteditable="true"]')) return;
  const key = event.key.toLowerCase();
  if (key === 'p') palette();
  else if (key === 's') stage();
  else if (key === 'f') {
    const action = document.fullscreenElement ? document.exitFullscreen() : html.requestFullscreen();
    action?.catch(() => {});
  } else presenter?.key(event);
});
route();
