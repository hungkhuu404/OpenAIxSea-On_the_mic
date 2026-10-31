# Declarations

**Unsigned draft for owner review.** Evidence snapshot: `b639793`. Proposed declaration wording below is explicitly separated from verified repository records and owner-confirmed provenance. Outstanding TODOs must be resolved before this becomes a submission declaration.

## Context files written before the event

The rule keeper has confirmed that these context files contain no code. The following declaration retains its separate provenance and final-clause checks:

> `AGENTS.md` and `docs/contracts.md` were written before the event as context files for Codex. They describe the project's conventions, rules and data contracts so that Codex can adapt to the project as fast as possible and the team spends as little time as possible on setup. They contain no code; all code in this repository was written during the event.

TODO (rule keeper): confirm the last clause against the git history before submission.

TODO (rule keeper): confirm the pre-event provenance of these context files and the event boundaries; commit timestamps alone do not prove when source material was written.

The initial commit is `65df901`, with author time **`2026-10-31T09:59:23+07:00`**, copied exactly from `git log --reverse --format="%h %aI %an %s"`. Its subject identifies the context files and imported data. The paragraph above is not independent proof of code authorship.

## Existing project and imported data

Only `data/` was imported, unmodified, from [OpenAIxSea-On_the_mic-data at commit `7663e92`](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic-data/tree/7663e92), as recorded in the [repository README](../README.md#prebuilt-data-declared) and confirmed by the rule keeper. The initial import is recorded in commit `65df901` at author time `2026-10-31T09:59:23+07:00`. Upstream `tools/` and `experiments/` were not copied.

The initial commit contains only `AGENTS.md`, `docs/` and `data/` at its root. Its data tree and the data tree at the reviewed snapshot are both `fed4ecc8b2c17a4590338eb52a8f2da7f6d90e01`, matching the upstream data tree at `7663e92` confirmed by the rule keeper. The local verification commands are:

```sh
git rev-parse 65df901:data
git rev-parse HEAD:data
git diff --exit-code 65df901 HEAD -- data
git ls-tree --name-only 65df901
```

Present data folders and their provenance records are:

| Material | Repository source |
|---|---|
| Law corpus, anchors and effective-date provenance | [Law source register](../data/law/SOURCES.md) |
| Product registry and recall decisions | [Registry source register](../data/registry/SOURCES.md) |
| Product evidence packs and public golden cases | [Case and evidence source register](../data/cases/SOURCES.md); `data/evidence/` and `data/cases/` |
| Sealed evaluation commitment, restricted here to hashes and aggregate metadata | [FREEZE.json](../data/cases/FREEZE.json) |
| Kera case material and reenactment transcript | [Kera source register](../data/demo/kera/SOURCES.md); `data/demo/kera/` |

The freeze metadata records root SHA-256 `8041bc9bfec8051492ef366fab73832a5abf584e1a280103e66e1ad878c197de`. This is a commitment, not a performance measurement. No sealed case content was consulted for this draft.

Labels were proposed with an AI assistant, cross-checked blind by a second model and confirmed by a team member.

Source: [data repository README at `7663e92`, line 21](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic-data/blob/7663e92/README.md#L21), checked and confirmed by the rule keeper.

## Built during the event

Post-initial commits record additions of shared models, tests, text normalization, the rule pack, the LLM/cache layer, date-specific context, extraction, routing/replay, the two-vote judge, legal guardrails, aggregation, the ledger, API, UI, scorer, gate checks, human labels, correction memory and clustering. This is a statement about the recorded implementation history, not proof of when every line was originally authored. Implementation evidence is linked in the [write-up](WRITEUP.md#implemented-path-and-evidence).

TODO (rule keeper): confirm against git log at submission.

TODO (rule keeper): verify the gate, adaptive loop and draft-rule skill before declaring them built and integrated; the current contracts and planned interfaces do not establish their completion.

## Open-source dependencies and fonts

Direct dependency names and exact pinned versions come from [requirements.txt](../requirements.txt). Licence identifiers were checked and confirmed by the rule keeper from PyPI package metadata for those exact releases; each source below links to that release's metadata.

| Library | Version | Licence | Source |
|---|---|---|---|
| fastapi | 0.162.0 | MIT | [PyPI package metadata](https://pypi.org/pypi/fastapi/0.162.0/json) |
| uvicorn | 0.54.0 | BSD-3-Clause | [PyPI package metadata](https://pypi.org/pypi/uvicorn/0.54.0/json) |
| pydantic | 2.14.1 | MIT | [PyPI package metadata](https://pypi.org/pypi/pydantic/2.14.1/json) |
| openai | 3.41.0 | Apache-2.0 | [PyPI package metadata](https://pypi.org/pypi/openai/3.41.0/json) |
| httpx | 0.28.1 | BSD-3-Clause | [PyPI package metadata](https://pypi.org/pypi/httpx/0.28.1/json) |
| pytest | 9.2.0 | MIT | [PyPI package metadata](https://pypi.org/pypi/pytest/9.2.0/json) |
| rapidfuzz | 3.14.6 | MIT | [PyPI package metadata](https://pypi.org/pypi/rapidfuzz/3.14.6/json) |

The bundled WOFF2 font families have readable licence files in `ui/fonts/`:

| Font | Licence | Repository evidence |
|---|---|---|
| Be Vietnam Pro | SIL Open Font License 1.1 | [Licence](../ui/fonts/be-vietnam-pro-OFL.txt) |
| JetBrains Mono | SIL Open Font License 1.1 | [Licence](../ui/fonts/jetbrains-mono-OFL.txt) |
| Newsreader | SIL Open Font License 1.1 | [Licence](../ui/fonts/newsreader-OFL.txt) |

## Third-party content

The data source registers document press quotations, court-report quotations and official material; their stated confidence and provenance limitations remain relevant. Public-facing speaker references use role labels such as `[KOL 1]`, `[KOL 2]` and `[KOL 3]`. The reenactment is not an original recording, and constructed lines must be identified as constructed. [Case sources](../data/cases/SOURCES.md), [Kera sources](../data/demo/kera/SOURCES.md), [attribution and presentation contracts](contracts.md#12-presenter-assets-local-only-and-hotkeys).

The presentation contract keeps any news clip on the presenting laptop rather than in the public repository. This draft has not inspected a clip or confirmed its actual use.

TODO (rule keeper): clip source line.

TODO (rule keeper): verify the selected clip's provenance, permission to present it and local-only storage before making a third-party-content declaration.

## Demonstration

The documented deployment is a local application on the presenting laptop. The [merged local-interface implementation](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/24) establishes an implementation record, not proof of a completed laptop demonstration.

TODO (rule keeper): confirm the actual local run and demonstration status on the presenting laptop; do not declare that the demo passed without evidence.

TODO (rule keeper): demo video link.
