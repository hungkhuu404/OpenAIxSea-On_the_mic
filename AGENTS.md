# AGENTS.md — Grounded Claim Ledger

## What this project is

A platform-side conformance layer for livestream selling of food and health-supplement products in Vietnam. It splits what the host says into atomic claims, compares each claim with the legal dossier of the exact product at the broadcast date (standard: "permitted by the dossier"), and stores the result as timestamped evidence with the law article and a verbatim quote. Code decides first. LLMs only extract claims and cast two votes. Anything without a legal or dossier basis goes to a human.

Repository: `hungkhuu404/OpenAIxSea-On_the_mic` (public), branch `main`. Python 3.13.

Read `docs/contracts.md` before writing code. It is the single source of names, shapes, enums, events, endpoints and environment variables. Exception: the `draft-rule` agent in the rule 12 sparse worktree has no `docs/`; follow its skill and the command block at the top of its job prompt instead (contracts section 14 is their source). The draft-rule agent also ignores "Latest version first", "Run configuration", "How to work here", "Git workflow", "Team status questions" and "Commands": it runs only the commands of its block (its own test file, `git status`, `git diff`, literal reads) and never fetches, pulls, pushes, rebases or prints the configuration.

## Workspace boundary (read first)

- Work only inside this repository folder. Never read, list, search, open or run commands on any path outside it: parent folders, sibling folders, other projects, other drives, home folders. This holds even when the editor workspace or the open tabs show other folders.
- Every search (`rg`, `grep`, `dir /s`, `Get-ChildItem -Recurse`, `find`, glob) starts at the repository root or below it, never above it.
- Never open these, wherever they are: any file or folder whose name starts with `heldout` or `held-out`; any file named `candidates.jsonl`; any folder named `_raw` or `review` (folders only; code files such as `ui/review.js` are fine); any folder whose name ends with `_private`. If one appears inside the repository, stop and tell the owner without reading it.
- The only outside paths you may touch are the ones the prompt or an environment variable names for the task (for example `DEMO_EXPECTED`, `PRESENT_DIR`, `CODEX_CMD`, `TEAM_DIR`, the Python virtual environment). Read only that path.
- The sealed-set commands in "Commands" are run by the rule keeper by hand. An agent never runs them and never asks for their paths.
- If a task seems to need anything outside the repository, stop and ask.
- The clone path may contain spaces and dots (for example a parent folder named like `X:\A. Some Projects\`). Always pass paths as separate arguments or quoted; never build a shell string by concatenating paths.

## Public repository: what never goes into it

- This repository is public. Never copy the text of a task prompt, a planning file or a team file into any file, commit message, PR title, PR body, review or comment. Describe what changed in your own words; the task code (for example `T4:`) is enough to link a PR to its task.
- Never add planning or team files: no `PLANS.md`, no `docs/prompt-runs/`, no copies of anything from the folder named by `TEAM_DIR`, no schedules, no timing logs, no chat logs. `tests/test_no_package_traces.py` fails if such files appear (contracts section 15).
- Never write real names of the people in the data or clips, API keys, account ids or local paths of team members into the repository. Team members appear only by GitHub handle (authorship, `labeler`, `approved_by`).

## Latest version first

- Before you read code to answer a question, report status, write or change code, or review a PR: run `git fetch origin`, compare `HEAD` with `origin/main` (`git rev-list --left-right --count HEAD...origin/main`), and state in your answer the commit you used (short sha) and whether it is behind `origin/main`.
- Bring your checkout up to date only with `git pull --ff-only`, and only when `git status --porcelain` is empty and no other Codex thread is working in the same folder. Otherwise say "not updated: <reason>" and work on, or answer from, the commit you named.
- Never use an answer, a file or a test result from an older commit as if it described `origin/main`.

## Run configuration (environment variables)

- The app reads configuration only from environment variables, through `core/config.py`. There is no `.env` file: never create one and never rely on one.
- Only four variables are stored per machine (`setx` on Windows, `~/.zshrc` on macOS): `TEAM_DIR`, `LLM_BACKEND`, `CODEX_CMD`, `LLM_TIMEOUT_S`. Every mode flag (`OFFLINE`, `LOOP_DRY`, `REGRESSION_AUTOCOMMIT`, `LOOP_OPEN_PR`, `PUBLIC_MODE`, `VIDEO_MODE`, `JUDGE_MODE`, the `*_MODEL` and `*_EFFORT` settings, `EVAL_DIR`, `LEDGER_DB`, `PRESENT_DIR`, `WORKTREE_ROOT`) is set only for one command or one terminal session, never with `setx`, never in a profile file.
- `python -m core.config` prints the one-line run configuration (contracts section 3.1). Put that line in every report of a live run, a demo test, a gate run, a draft-rule job or a stage step.
- Never print or store `OPENAI_API_KEY`; the configuration line shows only whether it is set.

## Hard invariants (tests enforce them; never break them)

1. Red is produced only by code (rule modules and session flags). No LLM output can ever produce red.
2. Asymmetric autonomy. Without a human the system may only become more careful: turn a rule off, or move verdicts to abstain. Anything that lets it decide more (a new rule, a dossier change that makes verdicts greener) needs a human. Every rule in a live pack must carry `approved_by`; a test checks this. A proposed rule may be loaded only in measure mode (gate, dry-run, oracle) and its results are never stored as live verdicts. Recalls apply immediately; greener dossier changes wait for approval.
3. Eval `as_of`: use the case's `source_date`; for every case of SKU `kera` use `2025-01-08`; cases with no `source_date` use `2026-10-21`.
4. Step F keeps a legal-basis anchor only if the law file's `effective_from` is on or before `as_of`. Never read `text_version_effective_from`.
5. R06 fires only when the normalized category is `TPBVSK` and `ad_confirmation.status` is `absent`. Food supplements and `unknown` never trigger R06.
6. R04 triggers and the mandatory negative tests are exactly those in the "R04 triggers" section below.
7. Legal basis for every rule comes from `rules/law_basis.json` (table below). A static test rejects forbidden anchors.
8. Never fold diacritics in steps A, B or F. `match_key` only locates text; display always uses the original NFC string.
9. Quotes are taken from P by `permitted_id`. P never contains items with `confidence = low`.
10. Judge prompts receive only the axis slice of P: no `gaps`, `regulator_warnings`, `note`, `source_detail`, `label_claims_not_in_declaration` or any regulator statement, and no dynamic few-shot examples.
11. Every LLM call goes through `pipeline/llm.py` and its cache. The rule keeper announces three freezes during the day: the S1 prompt, the step E prompt, then every LLM input. After the last one nothing that changes an LLM request may change: prompts, schemas, models, `JUDGE_MODE`, how P items are rendered, the axis-slice table, `textnorm.match_key`, how atoms are located; only code that runs after the LLM may change.
12. The `draft-rule` agent works in a sparse worktree that contains only `AGENTS.md`, `pytest.ini`, `requirements.txt`, `core/`, `textnorm/`, `rules/*.py`, `rules/collisions.txt`, `rules/law_basis.json`, `data/law/`, `.agents/skills/draft-rule/` and its own `loop_input/`. It only creates its new rule module, meta file and test file. It never reads anything else (no `tests/`, `gate/`, `eval/`, `cache/`, `data/cases/`, `data/demo/`, `data/evidence/`, `reports/`, `.local/`, other rule meta files) and never runs the gate. The job prompt it receives on stdin begins with the allowed-command block of contracts section 14, the same text as the "Allowed commands" section of its skill and the top of `retry.md`; gate check 7 accepts exactly that grammar. Instructions and gate are changed together, never one without the other.
13. Never modify `data/` or `eval/` by hand or by an agent: `eval/` is written only by the app, and only the loop owner's machine commits it. The sealed held-out set never enters this repo, any prompt or the repo cache; oracle runs write nothing inside the repository (no ledger, no logs, no cache).
14. Cards and UI never show fine amounts. Use `pipeline.guardrail.display_text`.
15. Similarity never decides a colour. `near_hits` are used only to rank the review queue, to suggest rules to humans and to cluster items for T5.
16. Every highlighted span (`span`, `evidence_span`, `law_span`) is a substring of its source, checked by code.
17. Real people: the repo, the data and the public link use role labels (`[KOL 1]`, `[KOL 2]`, `[KOL 3]`). Real names, video clips, subtitles, translations and slides exist only in the local `PRESENT_DIR` and are never committed. A real name may be shown only on a turn whose `attribution` is `press_attributed`; quotes the press does not attribute show "Speaker unknown"; team-written reenactment lines never carry a real name. Exception for event day, 31 October 2026, authorized by the rule keeper (`hungkhuu404`): the stage app on the presenting laptop, run with `PRESENT_DIR` set and `OFFLINE=1`, may be exposed through a temporary public tunnel link for judges, showing clips, slides and real names. These assets are still never committed; close the tunnel after judging.
18. Public mode (`PUBLIC_MODE=1`) never calls an LLM or Codex.

## Law basis table (`rules/law_basis.json`)

All anchor ids exist in `data/law/index.json`. F keeps those effective at `as_of`.

| Rule | Anchors | Notes |
|---|---|---|
| R04 | `LDUOC-2016/D6/K15` | Clause 15 unchanged since 2016 |
| R05 (category `TPBVSK`) | `ND15-2018/D27/K6`, `ND15-2018/D27/K2` | |
| R05 (category `thuc_pham_bo_sung`) | `LQC-2012/D8/K9`, `ND15-2018/D27/K2` | Primary basis first |
| R06 | `ND15-2018/D26/K1`, `ND15-2018/D27/K1` | |
| R08 | `ND15-2018/D27/K2` for `as_of` before 2026-05-15; `ND87-2026/D71/K4` from 2026-05-15 | ND87 D71 K4 covers patient testimony only |
| R09 | `LQC-2012/D8/K11`; plus `ND87-2026/D50/K2/a` from 2026-05-15 | |
| `L1_PREDECL` (12/12 flag) | `ND15-2018/D4/K1`, `ND15-2018/D5/K2/b` | `ND115-2018/D22/K3` is never shown |
| `L1_RECALL` | `ND15-2018/D4/K1`, `ND15-2018/D5/K2/b` | Same obligation basis as `L1_PREDECL` |
| `STANDARD` (green, category `thuc_pham_bo_sung`) | `ND15-2018/D27/K2` | The clause a green claim complies with |
| `STANDARD` (green, category `TPBVSK`) | `ND15-2018/D27/K6` | |

Forbidden anchors (static test, data file `gate/data/forbidden_anchors.json`), for this table and for every anchor of every T5 rule:
- amended or repealed clauses: `LQC-2012/D8/K3`, `LQC-2012/D8/K10`, `LQC-2012/D8/K13` (amended from 2026-01-01); `LDUOC-2016/D6/K2`, `LDUOC-2016/D6/K5/i`, `LDUOC-2016/D6/K8`, `LDUOC-2016/D6/K17`, `LDUOC-2016/D6/K18`, `LDUOC-2016/D6/K19` (per the file's `status_note`);
- `L122-2025/D22`, `L122-2025/D23`, `L122-2025/D24` and all their clauses: they apply only to livestream selling on e-commerce platforms, from 2026-07-01.

`law_span` (the phrase highlighted in the law column): for a red verdict, the `law_span` of the deciding rule's meta file — a phrase or an ordered list of phrases, the first one found in a kept anchor wins (R04 "chữa bệnh"; R08 ["bác sỹ, dược sỹ", "người bệnh"]; R09 "duy nhất"); for yellow (R05) and green (`STANDARD`), by category: food supplements "phù hợp với công dụng, tác dụng của sản phẩm đã được công bố" in `ND15-2018/D27/K2`; `TPBVSK` "chỉ được quảng cáo phù hợp với nội dung đã được xác nhận" in `ND15-2018/D27/K6`. Each `law_span` must be a substring of its anchor text after `textnorm.law_normalize` (CI checks). Step F returns abstain `LAW_NOT_EFFECTIVE` only when a family has rows but none is effective at `as_of`; a family with no rows at all is a programming error.

## R04 triggers (from Luật Dược Article 6 clause 15)

- Multi-syllable phrases with diacritics, matched on `match_key` as whole syllables: "phòng bệnh", "chữa bệnh", "chữa khỏi", "chẩn đoán bệnh", "điều trị", "đặc trị", "trị bệnh", "giảm nhẹ bệnh", "thuốc tây", "thay thế thuốc".
- Never the single syllables "trị" or "chữa". "khỏi" alone is not a trigger.
- "phòng bệnh" does not fire when the next syllable is "viện", "nhân", "án" or "xá" (a hospital, patient or infirmary room). This guard applies only to "phòng bệnh": "chữa bệnh nhân" is still a treatment claim.
- Exemption: the mandatory disclaimer of `ND15-2018/D27/K3/a` ("Thực phẩm này không phải là thuốc và không có tác dụng thay thế thuốc chữa bệnh"), matched fuzzily (longest common subsequence of syllables covering at least 75% of the disclaimer), and clause-level explicit negation. Ambiguous negation goes to the narrow LLM question (enum: negated, not negated, unsure; unsure → abstain `RULE_AMBIGUOUS`).
- Mandatory negative tests (must not fire): "giá trị dinh dưỡng", "trị số đường huyết", "trị giá", "quản trị", "sửa chữa", "có chứa", "duy trì", "phòng bệnh viện", "phòng bệnh nhân", golden `dk_betics_gold-018` and `dk_betics_gold-029`.
- Expected on golden (sanity check, not a target to tune): contiguous triggers catch 8 of the 11 golden R04 cases and fire on none of the 37 non-red cases.

## Repository map

See `docs/contracts.md` section 2 for owners. Folders: `core/`, `textnorm/`, `pipeline/`, `rules/`, `gate/`, `loop/`, `ledger/`, `api/`, `ui/`, `scripts/`, `tests/`, `eval/`, `cache/llm/`, `reports/`, `.agents/skills/`, `data/` (read-only), `.local/` (gitignored).

## How to work here

- One branch per task, named `<owner>/<task-code>-<short-task>`. Open a PR; CI must be green before merge. Keep PRs small. Follow "Git workflow" below.
- Rule approval belongs to the rule keeper (Hưng, GitHub `hungkhuu404`), who sets `approved_by` and `approved_at` in the rule's meta file in a separate batch PR (see "Git workflow").
- Do not change `docs/contracts.md` or `core/` without the contract owner (Hưng).
- Tests run offline: `OFFLINE=1`. `tests/conftest.py` pins every mode flag to its default for each test (contracts section 3); a test that needs a flag sets it itself. The `draft-rule` agent runs only the exact test commands in its skill, without environment-variable prefixes. Unit tests set the backend they need themselves. Anything that replays the committed cache (CI, the gate, the demo test, stage preparation, public mode) runs with `OFFLINE=1` and `LLM_BACKEND` equal to the backend that produced that cache (for this repository `codex`, the repository variable `CACHE_BACKEND`), because the backend is part of the cache key. Tests that need the network are marked `live`; the demo test is marked `demo`; both are excluded by default.
- Do not add dependencies beyond `requirements.txt` without asking.
- Windows first (the presentation laptop runs Windows; one teammate uses macOS): use `pathlib`, UTF-8 for every file and pipe, no shell-specific scripts. Start Codex with `CODEX_CMD` and pass prompts on stdin, never as arguments. The Codex CLI version is the one pinned for the event (contracts section 3); never upgrade or downgrade it from a task.
- Never print or log API keys.

## Git workflow (every task)

- One task = one branch = one PR. The prompt ends with a line "Task code X. Branch Y."; use exactly that branch name, created from the latest `origin/main` (`git fetch origin`, then branch from `origin/main`). Push the new branch at once (`git push -u origin <branch>`) so the team can see the task has started.
- Before opening the PR: `git fetch origin`, `git rebase origin/main`, run the full test suite, fix until green.
- PR title starts with the task code (for example `T4: ...`); a fix of an earlier task uses that task's code plus `-FIX` (for example `D4-FIX: ...`), never another task's code. PR body: what changed in your own words, the test summary, and which task codes can now use it. No prompt text, no schedule, no timing.
- After opening the PR, wait for CI (`gh pr checks <number> --watch`). When CI is green, merge it yourself (`gh pr merge <number> --merge`). Never merge while CI is red or pending. If main moved and CI must run again, rebase and repeat.
- Authorship: commits, PR titles, PR bodies and comments carry only the human owner. Never add agent names, "Generated by" lines, tool signatures, or co-author trailers for agents.
- Morning rule-pack PRs (`rules/`, origin `law_compiled`) are merged like any other PR with `approved_by` null; the rule keeper approves them in one batch PR that fills `approved_by` and `approved_at`. A rule PR opened by a draft-rule job (origin `t5`) is never merged by its author or by an agent: it stays open until the sealed-set measurement and the rule keeper's approval, and only the rule keeper merges it. Tests must never assert that a real rule meta file is unapproved; test the unapproved case on a temporary copy.
- Never edit files owned by someone else (`docs/contracts.md` section 2). If a contract must change, stop and ask the contract owner; do not change `core/` or `docs/contracts.md` in a feature PR.
- Waiting for another owner's module: import it inside the function that needs it; only a `ModuleNotFoundError` naming that module means "not ready" (a route then returns 503 `{"todo": "<module>"}`). Never use `hasattr(...)`/`getattr(..., None)` checks on a module that exists, and never create files in another owner's folder. In tests, replace missing collaborators with fakes (monkeypatch); test the "not ready" path by simulating the missing module. Tests must pass both before and after the other module lands.

## Team status questions

- When someone asks about team progress or coordination (what to do next, wait or continue, who waits for whom, push or pull, who merged what, timing), follow the coordination notes in the folder named by the environment variable `TEAM_DIR` (its `DIEU_PHOI.md` explains how to answer; the person's own list is the `VIEC_*.md` file with their name). Answer in Vietnamese.
- `TEAM_DIR` is outside the repository. Read it only to answer; never copy its files or their text into the repository, a commit, a PR or a comment. Apply "Latest version first" before answering.
- Answering such a question never changes files or git state: no edit, commit, pull, rebase, checkout, push or merge until the person asks for that action.
- If `TEAM_DIR` is not set or `DIEU_PHOI.md` is missing, say so in one line and stop.

## Commands

| Task | Command |
|---|---|
| Print the run configuration (one line) | `python -m core.config` |
| Tests | `pytest -q` (excludes `demo` and `live`) |
| Demo test | `pytest -m demo -q` (needs `DEMO_EXPECTED` for full comparisons) |
| Run the app | `python -m api` (add `--public` for public mode; default port 8000) |
| Ingest and process Kera | `python -m pipeline.cli kera --as-of 2025-01-08` |
| Process one session (again after a code or prompt change: `--reprocess`) | `python -m pipeline.cli process --session <id> --purpose live [--reprocess]` |
| Measure step E stability on one turn (fresh calls, no ledger writes) | `python -m pipeline.cli judge-repeat --session <id> --turn <turn_id> --n 5` |
| Fill the cache for public texts | `python -m pipeline.cli shadow` (golden, Kera reenactment, correction memory) |
| Score golden | `python -m gate.score --mode replay` |
| Gate one candidate rule | `python -m gate.run --rule <RULE_ID> --candidate-dir <path>` |
| Freeze a chosen set of correction-memory items as a cluster file (committed, with time) | `python -m loop.cli make-cluster --items <id,id,id> --name <name>` |
| Run a draft-rule job on a committed cluster file | `python -m loop.cli run-cluster --file eval/clusters/<name>.json` |
| Demo state | `python -m scripts.demo_state snapshot --name <n>` / `restore --name <n>` (app stopped first) / `prepare --exclude-rule <RULE_ID>` / `export --name <n> --to <dir>` / `import --from <dir>` |
| Sealed-set measurement (rule keeper only, two fixed times, never an agent) | first: `python -m gate.score --mode llm --purpose oracle --cases <outside repo> --cache-dir <outside repo> --pack v1 --aggregate-only`; second: `python -m gate.oracle replay --heldout <outside repo> --cache-dir <outside repo> --v2-rules <one rule id> --candidate-dir <checkout of the rule branch, outside repo> --out <outside repo>` |

## Data you will meet

- `data/law/index.json`: list of anchors `{id, file, text}` (for example `ND15-2018/D27/K2`). Law files under `data/law/<doc>/dieu-<n>.md` have YAML front matter with `effective_from`, sometimes `text_version_effective_from` (ignore it), `status`, `status_note`. `data/law/normalize.md` defines the normalization used for the "docstring is a verbatim substring of the law file" check.
- `data/evidence/<sku_id>.json`: `category`, `ad_confirmation.status`, `permitted_claims[]`, `ingredients_declared[]`, `usage_declared[]` (some packs), `recalls[]`, each item with `confidence`. Answer-leaking fields that never enter prompts: `gaps`, `regulator_warnings`, `note`, `source_detail`, `label_claims_not_in_declaration`.
- `data/demo/kera/dossier.json`: the Kera dossier by date (`timepoint_states`, `validity`, `self_declaration`, `ingredients_declared`, `nutrition_declared`, `label_claims`). `data/demo/kera/reenactment_script.md`: 24 turns; each turn is a line starting with `**[KOL n]**`; cut each turn at its first backtick.
- `data/cases/golden.jsonl`: 89 public cases with `case_id`, `sku_id`, `text`, `label`, `abstain_expected`, `rule_ids`, `source_date`, `modality`. `legal_basis` is an informative string, not anchor ids.

## Vietnamese terms

TPBVSK = health-protection food (needs an ad-content confirmation, GXNQC). Thực phẩm bổ sung = food supplement (self-declared; compared with the self-declaration, "bản tự công bố"). Hồ sơ = dossier. Công dụng = declared function. ĐKSP = product registration number. Thu hồi = recall.
