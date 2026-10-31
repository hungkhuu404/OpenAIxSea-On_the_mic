# Declarations

## Context files written before the event

`AGENTS.md` and `docs/contracts.md` were written before the event as context files for Codex. They describe the project's conventions, rules and data contracts so that Codex can adapt to the project as fast as possible and the team spends as little time as possible on setup. They contain no code; all code in this repository was written during the event.

TODO (rule keeper): confirm the last clause against the git history before submission.

## Existing project: prebuilt data

The prebuilt project is [OpenAIxSea-On_the_mic-data at commit `7663e92`](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic-data/tree/7663e92). Its `data/` tree was imported unmodified in the first commit, `65df901`, with author time `2026-10-31T09:59:23+07:00`, and has not changed since. That commit contains only `AGENTS.md`, `docs/` and `data/`. The data repository's `tools/` and `experiments/` folders were not copied. [Import declaration](../README.md#prebuilt-data-declared)

The imported material comprises:

- `data/law/`: the law corpus, anchor index and effective-date provenance. [Sources](../data/law/SOURCES.md)
- `data/registry/`: product registry records and recall decisions. [Sources](../data/registry/SOURCES.md)
- `data/evidence/` and `data/cases/`: product evidence packs, the public golden set and the sealed held-out commitment. [Sources](../data/cases/SOURCES.md)
- `data/demo/kera/`: the Kera case dataset, dated dossier and reenactment transcript. [Sources](../data/demo/kera/SOURCES.md)

The commitment in [FREEZE.json](../data/cases/FREEZE.json) records aggregate populations of n=89 public golden cases and n=44 sealed cases, with file hashes and root SHA-256 `8041bc9bfec8051492ef366fab73832a5abf584e1a280103e66e1ad878c197de`. These are committed dataset sizes, not measured results.

Labels were proposed with an AI assistant, cross-checked blind by a second model and confirmed by a team member.

The label statement comes from the [data repository README at `7663e92`](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic-data/blob/7663e92/README.md#L21), as confirmed by the rule keeper. The first commit's identifier and author time come from `git log --reverse --format="%h %aI %an %s"`.

## Built during the event

The commits after the initial import add the application code, tests, rule pack, gate, adaptive loop, UI and draft-rule skill. They include the shared models and normalization, claim extraction and routing, dossier context, two-vote comparison, legal guardrails, ledger and API, review queue, clustering and isolated rule drafting.

TODO (rule keeper): confirm against git log at submission.

## Open-source libraries and fonts

The application pins these direct dependencies in [requirements.txt](../requirements.txt). The rule keeper confirmed their licences from PyPI package metadata for the exact releases.

| Library | Exact version | Licence | Source |
|---|---|---|---|
| fastapi | 0.162.0 | MIT | [PyPI package metadata](https://pypi.org/pypi/fastapi/0.162.0/json) |
| uvicorn | 0.54.0 | BSD-3-Clause | [PyPI package metadata](https://pypi.org/pypi/uvicorn/0.54.0/json) |
| pydantic | 2.14.1 | MIT | [PyPI package metadata](https://pypi.org/pypi/pydantic/2.14.1/json) |
| openai | 3.41.0 | Apache-2.0 | [PyPI package metadata](https://pypi.org/pypi/openai/3.41.0/json) |
| httpx | 0.28.1 | BSD-3-Clause | [PyPI package metadata](https://pypi.org/pypi/httpx/0.28.1/json) |
| pytest | 9.2.0 | MIT | [PyPI package metadata](https://pypi.org/pypi/pytest/9.2.0/json) |
| rapidfuzz | 3.14.6 | MIT | [PyPI package metadata](https://pypi.org/pypi/rapidfuzz/3.14.6/json) |

The UI bundles WOFF2 files for these font families, each with its licence in `ui/fonts/`:

| Font | Licence | Licence file |
|---|---|---|
| Be Vietnam Pro | SIL Open Font License 1.1 | [Licence](../ui/fonts/be-vietnam-pro-OFL.txt) |
| JetBrains Mono | SIL Open Font License 1.1 | [Licence](../ui/fonts/jetbrains-mono-OFL.txt) |
| Newsreader | SIL Open Font License 1.1 | [Licence](../ui/fonts/newsreader-OFL.txt) |

## Third-party content

The data includes press quotations, quotations reported from court proceedings and official records. Their sources and confidence levels are recorded in [the case source register](../data/cases/SOURCES.md), [the Kera source register](../data/demo/kera/SOURCES.md), [the law source register](../data/law/SOURCES.md) and [the registry source register](../data/registry/SOURCES.md). Public speaker references use role labels such as `[KOL 1]`, `[KOL 2]` and `[KOL 3]`; constructed reenactment lines are identified as constructed.

A news clip is shown only from the presenting laptop and is never included in the repository. [Local presentation contract](contracts.md#12-presenter-assets-local-only-and-hotkeys)

TODO (rule keeper): clip source line.

TODO (rule keeper): confirm the selected clip's provenance and permission to present it.

## Demo

The app runs locally on the presenting laptop. Its public mode serves replay and code-only checks; presenter assets remain local. [Run instructions](../README.md#how-to-run), [presentation contract](contracts.md#12-presenter-assets-local-only-and-hotkeys)

TODO (rule keeper): confirm the actual laptop run and demonstration outcome before submission.

TODO (rule keeper): demo video link.
