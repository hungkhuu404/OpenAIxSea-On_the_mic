# Grounded Claim Ledger interface

## Purpose and hierarchy

The primary task is to inspect a livestream claim beside its dossier evidence and legal basis at the broadcast date. Keep the session ledger ahead of standalone sentence checking. The interface is a review workspace, with source text and provenance given more space than pipeline internals.

1. Workspace navigation and an explicit replay/public-mode notice.
2. Session selection, followed by a separate sentence-check disclosure.
3. Session identity, product, date, source, and counts of current claim verdicts.
4. A bounded transcript alongside the selected turn's evidence cards.
5. Pipeline details available through an accessible disclosure.

## Visual system

- Default new visitors to Obsidian / Acid: a near-black cold canvas, blue-gray metal surfaces, and an electric citron accent. Violet / Ice provides a distinct alternative with deep violet surfaces, lavender-gray text, and an ice-blue accent. Retain Cold Case, Ivory/Navy, White/Cobalt, Paper/Forest, and Slate/Dark. Preserve existing `palette-v3` choices; the labelled selector and P shortcut access all seven. A valid `?theme=` launch link selects and saves its theme, then removes only that query parameter while preserving the route. All palette colors live in `ui/styles/tokens.css`.
- Use each theme's accent for navigation, selection, focus, and actions. Verdict meanings stay consistent across themes: green, amber, red, and slate. Green, amber, and red badges use solid fills with contrasting text and their existing shapes. Needs human remains unfilled with a stronger dashed outline.
- Outcome summaries use colored counts, a tinted surface, and a solid top border; the Needs human summary remains unfilled and dashed. Verdict cards repeat the status in the header and top border. Text, icons, and shape must communicate the classification alongside color.
- Apply the same verdict tint to the entire transcript tile and claim card, including Speech, Dossier, Law, and provenance. Transcript tiles use the existing displayed verdict priority when a turn has several claims; each claim card retains its own recorded verdict. Selection and hover preserve the status hue. Needs human stays neutral with a dashed boundary; turns without a decision remain neutral.
- Obsidian / Acid, Violet / Ice, and Cold Case share a fine accent underline on selected navigation, an outlined ledger mark, and an inspection-desk layout. The two new palettes use stronger 18% verdict surface fills and 23% hover fills; older palettes preserve their existing tint levels. Speech and evidence stay clearly separated while each verdict's tint covers the whole card.
- Use the bundled Be Vietnam Pro for controls, Newsreader for original speech, and JetBrains Mono for identifiers, dates, and counts. No font or UI resource requires an external request.
- Use a 4px spacing base, 16–24px panel padding, and 24px main layout gaps. The three inspection palettes use 2–4px corners to resemble working documents and equipment; the earlier general-purpose themes retain 6px controls, 10px panels, and 16px overview corners.
- Surface and border tokens distinguish nested areas. Keep original quotes readable and allow identifiers and long evidence strings to wrap.
- Summary counts represent current per-atom verdicts, not confidence scores or approval rates. Preserve the four labels: Within file, Beyond file, Prohibited, Needs human. Color, shape, and text travel together; Needs human remains unfilled and dashed.

## Responsive behavior

- Wide screens pair a scrollable transcript with three evidence columns.
- Intermediate widths stack the evidence columns without changing their Speech → Dossier → Law order.
- Below 800px, the transcript sits above the evidence with bounded scrolling. At small phone widths, outcome counts become a two-column grid, metadata fields stack, and primary controls remain at least 44px high.
- Stage mode increases the existing text token. Layouts must accommodate that scaling and browser zoom without horizontal page scrolling.
- In the local clip player, keep the video beside the currently spoken claim and make the video sticky while evidence scrolls. Use a compact session context without the full transcript list or aggregate counts; the ledger retains the full transcript. A visible Play with sound control starts playback through a user gesture, and the native media controls remain available.
- Frame the video as an inspection monitor with its actual input identifier, a timecode driven by media time, and Ready/Playing/Paused/Ended status from playback events. Place LIVE REVIEW beside an explicit RECORDED CLIP label. Its neon-red circular light has a bright core and layered glow; it pulses slowly only during playback and stays static for reduced-motion users. The bezel adapts ThreeUI Community's gradient, inset highlight, and pointer flashlight. Keep the light behind content and away from video controls. Do not imply the recorded clip is a current broadcast or add fabricated viewers, rates, signal levels, or case statistics.

## Interaction and accessibility

- Provide a skip link, a labelled navigation region, and a visible current-page state.
- Keep keyboard focus visible. Announce only concise loading, search, and submit feedback, rather than placing the entire workspace in a live region.
- Search has a visible submit button, explicit keyboard help, and stale-request protection. Changing a query clears old matches before they can navigate the transcript.
- Sentence checking retains the original wording and clearly reports pending, completed, and failed requests. Controls prevent concurrent submissions.
- Prepared clips use their recorded, timed transcript. Hide prior stored assessments until playback has reached the corresponding line. After a line is heard through its end, submit only that turn to the existing processing endpoint; keep the tile neutral while the request is pending. Apply the whole-tile verdict color only after the server returns a complete assessment. This flow does not perform speech recognition or simulate intermediate model work.
- Keep the completed line and its evidence visible when the clip ends. Move to reenactment only through the explicit Continue control. Seeking must not submit skipped lines; replaying or selecting a completed line must not duplicate its request. A late response must not replace the line currently being watched.
- Distinguish unavailable model evaluation from a substantive Needs human verdict. Offline cache misses, timeouts, and failed requests retain the transcript and offer Retry without presenting a final verdict color.
- Apply Animate UI's CSS motion tokens: 150ms text fade/blur for a newly heard cue, 200ms evidence fade after an actual result, and short press feedback. Unchanged playback ticks preserve the transcript DOM. A 2px neon-red frame sits in the padding outside the video, pulsing on the same 1.6-second rhythm as LIVE during playback and staying illuminated when paused. It does not intercept controls or change verdict colors. Reduced motion removes entrances, press scaling, LIVE/frame pulsing, and pointer tracking. Keyboard focus remains immediately visible.
- Preserve public-mode restrictions, server-filtered law display, source attribution, and original Unicode text. The interface never infers a verdict, displays a fine amount, or replaces missing evidence with an invented legal basis.

## Implementation boundary

This system uses the existing vanilla JavaScript modules and CSS tokens. It adds no component framework, external asset service, or runtime dependency. Review and presentation screens share the same controls and surfaces; their approval and processing contracts remain in `docs/contracts.md`.

The selected Animate UI and ThreeUI effects are source adaptations. Their pinned upstream references, local changes, and license notices are recorded in `docs/third-party-ui.md` and `third_party/`.
