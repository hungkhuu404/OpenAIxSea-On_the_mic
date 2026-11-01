# Third-party UI source acknowledgments

## Animate UI

`ui/styles/motion.css` adapts motion tokens and CSS recipes from [Animate UI](https://github.com/imskyleen/animate-ui), pinned to commit `dec9850cf8684250d4c289af3e4b3865707ce986`:

- [Motion tokens](https://github.com/imskyleen/animate-ui/blob/dec9850cf8684250d4c289af3e4b3865707ce986/skills/animate-ui/references/tokens.md): `transition.swap` (150ms), `transition.fade` and `transition.color` (200ms), `ease.out`, `ease.color`, `blur.icon` (2px), and press scales (0.97 for buttons, 0.985 for rows).
- [Text swap](https://github.com/imskyleen/animate-ui/blob/dec9850cf8684250d4c289af3e4b3865707ce986/skills/animate-ui/references/recipes/swap.md) and [press recipes](https://github.com/imskyleen/animate-ui/blob/dec9850cf8684250d4c289af3e4b3865707ce986/skills/animate-ui/references/recipes/press.md), applied through the official skill's CSS-only path. The [Fade primitive](https://github.com/imskyleen/animate-ui/blob/dec9850cf8684250d4c289af3e4b3865707ce986/packages/react/src/primitives/effects/fade/index.tsx) provides the opacity entrance pattern.

New transcript cues receive a short opacity/blur entrance; actual returned evidence fades in. Unchanged playback ticks preserve the transcript DOM and do not restart the entrance. Button presses use the CSS tween fallback instead of the library's spring runtime. The app retains its stricter reduced-motion behavior: no animation or press scaling. No package, React component runtime, staged assessment, or synthetic typing delay is added.

The upstream MIT + Commons Clause notice is included in [animate-ui-LICENSE.md](../third_party/animate-ui-LICENSE.md). The skill's additional attribution is preserved in [animate-ui-NOTICE.md](../third_party/animate-ui-NOTICE.md).

## ThreeUI Community

The inspection monitor adapts selected effects from [MengTo/threeui](https://github.com/MengTo/threeui), pinned to commit `68802d5428071ada5c20db8094b1649e6bb770ed`.

- [Diagnostics Panel flashlight](https://github.com/MengTo/threeui/blob/68802d5428071ada5c20db8094b1649e6bb770ed/src/shaders/neuform-isolated/sources/diagnostics-panel.html#L33): a radial highlight positioned relative to the component. Its [pointer-coordinate implementation](https://github.com/MengTo/threeui/blob/68802d5428071ada5c20db8094b1649e6bb770ed/src/shaders/neuform-isolated/sources/diagnostics-panel.html#L189) is adapted in `ui/monitor-surface.js`.
- [Spinning Border Button surface](https://github.com/MengTo/threeui/blob/68802d5428071ada5c20db8094b1649e6bb770ed/src/shaders/neuform-isolated/sources/spinning-border-button.html#L33): the surface gradient and inset highlight are adapted for the monitor bezel. The spinning conic beam is not included.

The local adaptation uses native DOM events and CSS custom properties. Pointer updates are coalesced with `requestAnimationFrame`, enabled only for a fine pointer with hover, and disabled when reduced motion is requested. Disposing the monitor removes listeners, cancels queued work, and clears the pointer highlight. Decorations do not control playback, evaluation, or verdict colors.

ThreeUI's React wrappers, preview document, remote scripts, demo telemetry, fonts, images, and canvas render loops are not included. Colors are mapped to this application's design tokens.

The selected source is MIT licensed, copyright 2026 Meng To. The complete upstream notice is preserved in [third_party/threeui-LICENSE.txt](../third_party/threeui-LICENSE.txt); [upstream license](https://github.com/MengTo/threeui/blob/68802d5428071ada5c20db8094b1649e6bb770ed/LICENSE).
