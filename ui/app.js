import { el } from './dom.js';
import { request, setPresenter, events } from './data.js';
import { sessionScreen } from './session-screen.js?v=turn-fills-1';
import { clipScene } from './clip.js?v=signal-1';
import { presenterView } from './presenter.js?v=signal-1';
import { sentenceBox } from './preflight.js';
import { reviewView } from './review.js';

const app = document.querySelector('#app');
const html = document.documentElement;
let health = {}, presenter, stopEvents = () => {}, generation = 0;
const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* Storage is optional. */ } },
};
const palettes = ['obsidian', 'violet', 'coldcase', 'oil', 'cobalt', 'forest', 'graphite'];
function validPalette(value) { return palettes.includes(value) ? value : 'obsidian'; }
const requestedPalette = new URLSearchParams(location.search).get('theme');
html.dataset.palette = validPalette(palettes.includes(requestedPalette) ? requestedPalette : storage.get('palette-v3'));
if (palettes.includes(requestedPalette)) {
  storage.set('palette-v3', requestedPalette);
  const url = new URL(location.href); url.searchParams.delete('theme');
  history.replaceState(history.state, '', url);
}
async function corner() {
  const node = document.querySelector('#stage-corner');
  node.hidden = html.dataset.stage !== 'true';
  if (node.hidden) return;
  try {
    health = await request('/api/health');
    node.textContent = `${health.backend} · offline=${health.offline}${health.loop_dry ? ' · DRY RUN' : ''}`;
  } catch { node.textContent = 'Health unavailable'; }
}
function setPalette(value) {
  html.dataset.palette = validPalette(value);
  storage.set('palette-v3', html.dataset.palette);
  syncControls();
}
function palette() {
  const index = palettes.indexOf(validPalette(html.dataset.palette));
  setPalette(palettes[(index + 1) % palettes.length]);
}
function stage() {
  html.dataset.stage = String(html.dataset.stage !== 'true'); storage.set('stage', html.dataset.stage); corner();
  syncControls();
}
function syncControls() {
  document.querySelector('#palette').value = html.dataset.palette;
  document.querySelector('#stage').setAttribute('aria-pressed', String(html.dataset.stage === 'true'));
}
function pageHeading(title, description) {
  const heading = el('section', '', 'page-heading');
  heading.append(el('p', 'EVIDENCE WORKSPACE', 'eyebrow'), el('h1', title), el('p', description, 'page-description'));
  return heading;
}
document.querySelector('#palette').onchange = event => setPalette(event.target.value);
document.querySelector('#stage').onclick = stage;
syncControls();
document.querySelector('.skip-link').onclick = event => {
  event.preventDefault(); app.focus(); app.scrollIntoView({ block: 'start' });
};
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
      if (!root.isConnected) return;
      root.append(view.node);
      stopEvents = events(false, event => { if (['queue.changed', 'job.started', 'job.finished', 'job.attempt', 'rule.merged', 'backfill.done', 'dossier.pending'].includes(event.type)) view.refresh(); });
    }
  } catch { root.append(el('p', 'This scene is unavailable until its session is prepared.', 'notice')); }
}

async function route() {
  const current = ++generation;
  stopEvents(); stopEvents = () => {}; presenter = null;
  const loading = el('p', 'Loading workspace…', 'loading-state');
  loading.setAttribute('role', 'status');
  app.replaceChildren(loading);
  app.setAttribute('aria-busy', 'true');
  try {
    const nextHealth = await request('/api/health');
    if (current !== generation) return;
    health = nextHealth;
    const publicMode = Boolean(health.public);
    const isPresenter = !publicMode && location.hash.startsWith('#/present');
    setPresenter(isPresenter);
    document.querySelector('#present-link').hidden = publicMode;
    document.querySelector('#public-banner').hidden = !publicMode;
    html.dataset.stage = storage.get('stage') || String(Boolean(health.stage));
    syncControls();
    const activeLink = isPresenter ? 'present-link' : location.hash.startsWith('#/review') ? 'review-link' : 'ledger-link';
    for (const link of document.querySelectorAll('.primary-nav a')) {
      if (link.id === activeLink) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    if (activeLink !== 'ledger-link') document.querySelector('#time-context').textContent = isPresenter ? 'INSPECTION' : 'REVIEW';
    await corner();
    if (current !== generation) return;
    if (isPresenter) {
      const view = await presenterView((...args) => { stopEvents(); scene(...args); });
      if (current !== generation) return;
      presenter = view; app.append(view.node);
      const clip = new URLSearchParams(location.hash.split('?')[1]).get('clip');
      if (['a1', 'a2'].includes(clip) && view.node.isConnected) view.openClip(clip);
    } else if (location.hash.startsWith('#/review')) {
      app.append(pageHeading('Human review', 'Resolve uncertain claims, inspect rule proposals, and review the evidence behind each decision.'));
      let present = {};
      if (!publicMode) {
        try { present = (await request('/api/present/manifest')).present; } catch { /* Optional presenter defaults. */ }
      }
      if (current !== generation) return;
      const view = await reviewView({ publicMode, approver: present.approver, target: present.pr1_third_target });
      if (current !== generation) return;
      app.append(view.node);
      stopEvents = events(publicMode, event => {
        if (['queue.changed', 'job.started', 'job.finished', 'job.attempt', 'rule.merged', 'backfill.done', 'dossier.pending'].includes(event.type)) view.refresh();
      });
    } else {
      app.append(pageHeading('Session ledger', 'Follow a claim from what was said to what the dossier permits.'));
      const id = new URLSearchParams(location.hash.split('?')[1]).get('session');
      const workspaceTools = el('section', '', 'workspace-tools');
      const check = el('details', '', 'preflight-panel');
      const checkTitle = el('summary', '', 'preflight-toggle');
      checkTitle.append(el('span', 'Check a sentence'), el('small', 'Run a separate code check', 'muted'));
      check.append(checkTitle, sentenceBox());
      try {
        const sessions = await request('/api/sessions');
        if (current !== generation) return;
        if (sessions.length) {
          const label = el('label', publicMode ? 'Reenacted session' : 'Session', 'session-picker');
          const select = el('select');
          for (const row of sessions) {
            const option = el('option', `${row.as_of} · ${row.session_id}`);
            option.value = row.session_id; select.append(option);
          }
          select.value = id || sessions[0].session_id;
          select.onchange = () => { location.hash = `#/?session=${encodeURIComponent(select.value)}`; };
          label.append(select); workspaceTools.append(label);
        }
      } catch { /* The session screen can still show its explicit unavailable state. */ }
      if (current !== generation) return;
      workspaceTools.append(check); app.append(workspaceTools);
      let translations = {};
      if (!publicMode) {
        try { translations = (await request('/api/present/manifest')).translations; } catch { /* Optional local translations. */ }
      }
      if (current !== generation) return;
      await showSession(app, id, translations, {
        onNavigate: target => { location.hash = `#/?session=${encodeURIComponent(target)}`; },
      });
    }
  } catch {
    if (current !== generation) return;
    const unavailable = el('section', '', 'empty-state service-error');
    unavailable.setAttribute('role', 'alert');
    const retry = el('button', 'Try again'); retry.type = 'button'; retry.onclick = route;
    unavailable.append(el('h2', 'Workspace unavailable'), el('p', 'Check that the local service is running, then try again.', 'muted'), retry);
    app.append(unavailable);
  } finally {
    loading.remove();
    if (current === generation) app.setAttribute('aria-busy', 'false');
  }
}
window.addEventListener('hashchange', route);
window.addEventListener('keydown', event => {
  if (event.ctrlKey || event.metaKey || event.altKey || event.repeat) return;
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
