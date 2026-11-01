import { el } from './dom.js';
import { request, post } from './data.js';

export async function presenterView(showScene) {
  const root = el('section', '', 'presenter-workspace');
  let manifest;
  try { manifest = await request('/api/present/manifest'); }
  catch { manifest = { slides: [], clips: {}, translations: {}, present: {} }; }
  let index = 0, scene = 0, previous = 0;
  function slide() {
    root.replaceChildren();
    if (!manifest.slides.length) {
      root.append(el('p', 'Presentation assets are unavailable. The demo remains available.', 'notice'));
      const demo = el('button', 'Open demo'); demo.onclick = () => jump(1); root.append(demo);
      return;
    }
    const image = el('img', '', 'slide'); image.src = manifest.slides[index]; image.alt = `Slide ${index + 1}`;
    root.append(image);
  }
  function jump(next, backup = false) {
    previous = scene; scene = next;
    if (scene === 1) {
      const heading = el('header', '', 'inspection-heading');
      const title = el('div');
      title.append(el('p', 'CLAIM CONFORMANCE / INSPECTION', 'eyebrow'), el('h1', 'Inspection desk'));
      const context = el('p', '', 'inspection-context');
      context.append(el('span', `SOURCE ${backup ? 'A2' : 'A1'}`, 'mono'), el('span', 'Recorded evidence'));
      heading.append(title, context); root.replaceChildren(heading);
    } else {
      root.replaceChildren(el('h1', ['Slides', '', 'Kera · 2025-01-08', '2024-12-12', 'Recall notice (replay)', 'Review', 'Merge PR2'][scene]));
    }
    showScene(root, scene, backup, manifest);
  }
  slide();
  return { node: root, manifest, openClip(key) {
    if (['a1', 'a2'].includes(key)) jump(1, key === 'a2');
  }, async key(event) {
    const key = event.key.toLowerCase();
    if (key === 'arrowright') {
      event.preventDefault();
      if (index < manifest.slides.length - 1 && scene === 0) { index++; slide(); }
      else jump(1);
    } else if (key === 'arrowleft' && scene === 0) { event.preventDefault(); index = Math.max(0, index - 1); slide(); }
    else if (/^[1-6]$/.test(key) || (event.shiftKey && event.code === 'Digit1')) jump(event.shiftKey ? 1 : Number(key), event.shiftKey);
    else if (key === 'b') { const next = previous; if (!next) { previous = scene; scene = 0; slide(); } else jump(next); }
    else if (key === 'l') {
      const { pr1_third_target: target, labeler } = manifest.present;
      if (!target || !labeler) return;
      try {
        await post('/api/labels', { target, label: 'red', rule_id: 'R04', labeler });
        const dot = document.querySelector('#operator-dot'); dot.hidden = false;
        setTimeout(() => { dot.hidden = true; }, 1000);
      } catch { /* Keep the presentation surface unchanged. */ }
    } else if (key === 'escape') location.hash = '#/';
  } };
}
