// ThreeUI Community flashlight adaptation. Source and MIT notice:
// docs/third-party-ui.md and third_party/threeui-LICENSE.txt.
export function attachMonitorSurface(monitor) {
  if (!monitor?.addEventListener || !monitor.style || !globalThis.matchMedia ||
      !globalThis.requestAnimationFrame || !globalThis.cancelAnimationFrame) return () => {};
  const pointer = globalThis.matchMedia('(hover: hover) and (pointer: fine)');
  const reducedMotion = globalThis.matchMedia('(prefers-reduced-motion: reduce)');
  let disposed = false, frame = null, position = null;
  const enabled = () => pointer.matches && !reducedMotion.matches;

  function clear() {
    if (frame !== null) globalThis.cancelAnimationFrame(frame);
    frame = null; position = null;
    monitor.dataset.surfaceActive = 'false';
    monitor.style.removeProperty('--monitor-pointer-x');
    monitor.style.removeProperty('--monitor-pointer-y');
  }
  function move(event) {
    if (disposed) return;
    if (!enabled() || event.pointerType === 'touch') { clear(); return; }
    position = { x: event.clientX, y: event.clientY };
    if (frame !== null) return;
    frame = globalThis.requestAnimationFrame(() => {
      frame = null;
      if (disposed || !enabled() || !position) { clear(); return; }
      const rect = monitor.getBoundingClientRect();
      monitor.style.setProperty('--monitor-pointer-x', `${position.x - rect.left}px`);
      monitor.style.setProperty('--monitor-pointer-y', `${position.y - rect.top}px`);
      monitor.dataset.surfaceActive = 'true';
      position = null;
    });
  }
  function preferenceChanged() {
    if (!enabled()) clear();
  }
  clear();
  monitor.addEventListener('pointermove', move, { passive: true });
  monitor.addEventListener('pointerleave', clear);
  monitor.addEventListener('pointercancel', clear);
  pointer.addEventListener?.('change', preferenceChanged);
  reducedMotion.addEventListener?.('change', preferenceChanged);

  return () => {
    if (disposed) return;
    disposed = true;
    monitor.removeEventListener('pointermove', move);
    monitor.removeEventListener('pointerleave', clear);
    monitor.removeEventListener('pointercancel', clear);
    pointer.removeEventListener?.('change', preferenceChanged);
    reducedMotion.removeEventListener?.('change', preferenceChanged);
    clear();
  };
}
