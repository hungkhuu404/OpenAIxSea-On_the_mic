import { el } from './dom.js';
import { request, setPresenter, loadSession, events } from './data.js';
import { sessionView } from './session.js';
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

async function showSession(root, id, translations = {}) {
  const session = await loadSession(id, Boolean(health.public));
  if (!session) {
    root.append(el('p', 'Replay not ready. Prepared sessions will appear when the replay modules are available.', 'notice'));
    return;
  }
  let view = sessionView(session, translations, session.events || []);
  root.append(view.node);
  const latest = session.verdicts?.map(row => row.ts).sort().at(-1);
  document.querySelector('#time-context').textContent = session.live ? 'LIVE' : `REPLAY · processed ${latest ? new Date(latest).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '—'}`;
  if (!session.fixture) {
    let refreshing = false;
    stopEvents = events(Boolean(health.public), async event => {
      view.event(event);
      if (!['verdict', 'turn.verdict', 'session.flag', 'backfill.done'].includes(event.type) || refreshing) return;
      refreshing = true;
      try {
        const updated = await loadSession(session.session_id, Boolean(health.public));
        if (!root.isConnected) return;
        const next = sessionView(updated, translations);
        view.node.replaceWith(next.node); view = next;
      } catch { /* Keep the last confirmed evidence visible. */ }
      finally { refreshing = false; }
    });
  }
}
async function scene(root, number, backup, manifest) {
  try {
    if (number === 1) {
      const key = backup ? 'a2' : 'a1', clip = manifest.clips[key];
      if (!clip?.available) {
        root.append(el('p', 'Clip unavailable · synthetic demo', 'notice'));
        await showSession(root, null, manifest.translations);
        return;
      }
      const video = el('video'); video.controls = true; video.src = `/present/clip/${key}.mp4`; root.append(video);
      for (const line of [clip.speaker_line, clip.source_line]) if (line) root.append(el('p', line));
      if (clip.subtitles) {
        const response = await fetch(`/present/clip/${key}.subs.json`);
        const subtitles = await response.json();
        const caption = el('div'); root.append(caption);
        video.ontimeupdate = () => {
          const line = subtitles.find(item => item.start <= video.currentTime && item.end > video.currentTime);
          caption.replaceChildren(el('p', line?.vi || ''), el('p', line?.en || '', 'translation'));
        };
      }
    } else if (number <= 4) {
      await showSession(root, number === 3 ? 'kera-reenact-2024-12-12' : 'kera-reenact-2025-01-08', manifest.translations);
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
