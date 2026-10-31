# Contracts — Grounded Claim Ledger

This file is the single source of names and shapes. Every module codes against it. Changing it needs a PR approved by the contract owner (Hưng). Records below are implemented as pydantic v2 models in `core/models.py`; enums in `core/enums.py`; config in `core/config.py`; paths in `core/paths.py`.

## 1. Conventions

- Python 3.13 with exactly the versions in `requirements.txt`. One FastAPI + asyncio process. SQLite in WAL mode with FTS5.
- All text is stored as Unicode NFC. Display always uses the original NFC string. `match_key` and `fold` outputs are for matching and search only, never displayed.
- Dates are `YYYY-MM-DD`. Timestamps are ISO-8601 UTC. JSON files are UTF-8 without BOM, LF line endings.
- Code, comments, docs and UI strings in English. Vietnamese only inside data, quotes, law text and the Vietnamese subtitle line.
- No module performs network calls except `pipeline/llm.py` (LLM backends) and `loop/draft.py` (Codex). Tests never hit the network.
- Law facts: anchor ids and texts come from `data/law/index.json`; effective dates come only from the YAML front matter of each law file (`effective_from`; never `text_version_effective_from`), read only by `pipeline.guardrail.is_effective`; "verbatim" checks use `textnorm.law_normalize`, which implements `data/law/normalize.md`.
- IDs: `session_id` is a short slug (for example `kera-reenact-2025-01-08`); `turn_id` is `t01`, `t02`, …; `atom_id` is `<turn_id>.a<n>`; the global atom key is `<session_id>:<atom_id>`. `verdict_id` is an integer assigned by the ledger.

## 2. Repository layout and owners

| Path | Owner | Content |
|---|---|---|
| `core/` | Hưng | Shared models, enums, config, paths (this file) |
| `textnorm/` | Tân | `nfc`, `match_key`, `locate`, `fold`, `law_normalize`, teencode table |
| `pipeline/` | Tân | ingest adapters (`ingest.py`), S0 context, S1 extract, S2 router (A, B call, D), step E (`judge.py`), LLM layer, runner, replay, dossier events, raw path |
| `pipeline/ingest_clip.py` | Phúc | Clip transcript adapter (local-only presentation data) |
| `pipeline/guardrail.py`, `pipeline/aggregate.py` | Hưng | Step F guardrail and display text; S3 aggregation and `decided_by` |
| `rules/` | Đại (code), Hưng (`rules/law_basis.json`, approvals) | Rule modules, rule meta files, roles, near-hit detector, collision list, law basis table, `rules/_common.py` (shared source-bound helpers rule modules may import), optional `rules/equivalents.json` (human-approved equivalent phrasings for the step A allow-list; absent = empty) |
| `gate/` | Đại | Scorer, gate checks, oracle CLI, static test data |
| `loop/` | Đại | Labels to correction memory, T5 clustering and trigger, draft-rule runner, dry-run, merge |
| `ledger/` | Phúc | SQLite schema, append-only stores, FTS, event store |
| `api/` | Phúc | FastAPI app, SSE, queue ranking, presenter endpoints, public mode |
| `ui/` | Phúc | Static UI (vanilla JS modules + CSS tokens, no build step), self-hosted fonts |
| `scripts/` | Phúc | Demo state: snapshot, restore, prepare |
| `tests/` | each owner; `tests/demo/` Hưng | Unit tests; demo test (marker `demo`) |
| `eval/` | written by the app only | `regressions.jsonl` (correction memory) and `clusters/` (frozen cluster files with their runs). Never hand-edited. Only the loop owner's machine commits changes here |
| `cache/llm/` | written by the LLM layer | Committed cache rows for public texts only |
| `reports/` | Hưng | Oracle aggregate reports (no case texts, no case ids) |
| `.agents/skills/draft-rule/` | Đại | The draft-rule skill |
| `data/` | imported | Prebuilt data, read-only, never modified |
| `.local/` | gitignored | `ledger.db`, local cache, snapshots, logs |

## 3. Configuration (environment variables, read only in `core/config.py`)

| Variable | Default | Meaning |
|---|---|---|
| `LLM_BACKEND` | `off` | `api` (OpenAI API key), `codex` (`codex exec` with saved login, no API key), `off` (no LLM). The oracle on the sealed set never uses `codex`. The backend is part of the cache key, so every replay consumer (CI, gate, demo test, stage preparation, public mode) runs with `OFFLINE=1` **and** `LLM_BACKEND` set to the backend that produced the committed cache (for this repository `codex`); with `OFFLINE=1` no call is ever made, so no key is needed. CI takes that value from the GitHub repository variable `CACHE_BACKEND` (set to `codex` by the rule keeper at the first commit; the workflow default is `codex`; not an environment variable of the app). One backend per committed cache: `shadow` refuses to add rows of another backend to `cache/llm/`. Switching the whole cache to another backend is the rule keeper's call: set `CACHE_BACKEND`, every machine sets the same `LLM_BACKEND`, the full shadow is redone. The oracle never uses `codex` |
| `OPENAI_API_KEY` | unset | Runtime key for `api`. Separate from Codex accounts |
| `S1_MODEL` / `S1_EFFORT` | `gpt-6-luna` / `none` | S1 extraction and the narrow negation question `negq`. Model names are config only; check which models the account offers before use |
| `JUDGE_MODEL` / `JUDGE_EFFORT` | `gpt-6.1-sol` / `low` | Step E (T1, T2) |
| `JUDGE_MODE` | `two` | `two` (T1 ‖ T2) or `off` (step E returns abstain `LLM_OFF` without calls). Switched only by the rule keeper. There is no one-vote mode: yellow always needs two agreeing votes |
| `DRAFT_MODEL` / `DRAFT_EFFORT` | `gpt-6.1-sol` / `low` | Model for `draft-rule` (Codex), chosen by measurement |
| `LLM_TIMEOUT_S` | `6` | Live call timeout. Over → abstain `LLM_TIMEOUT`. Every team machine stores `60` (a `codex` call takes about 4–13 s) |
| `LLM_PARALLEL` | `8` | Max concurrent LLM calls in batch runs |
| `OFFLINE` | `0` | `1` = `pipeline.llm` never calls the network; a cache miss behaves like backend `off`. It applies only to `pipeline.llm`: the draft-rule loop is switched only by `LOOP_DRY` (with `OFFLINE=1` and `LOOP_DRY=0` a draft-rule job still starts Codex) |
| `LLM_CACHE_DIR` | unset | Overrides cache location (the oracle uses a directory outside the repo; `gate.score --cache-dir` and `gate.oracle --cache-dir` set it for their process) |
| `LLM_LOCAL_CACHE_DIR` | `.local/cache` | Local (uncommitted) cache. The demo test points it to an empty temp folder so that only committed rows are used |
| `LEDGER_DB` | `.local/ledger.db` | SQLite path. Purpose `oracle` never writes any ledger |
| `EVAL_DIR` | `eval` | Folder of `regressions.jsonl` and `clusters/`. Tests and the demo test point it to a temp folder |
| `PUBLIC_MODE` | `0` | `1` = public link: read-only, replay plus raw-path checks only, rate limited, no LLM, no Codex |
| `RATE_LIMIT_PER_MIN` | `20` | Public raw-path checks per client per minute |
| `PRESENT_DIR` | unset | Local-only presentation assets (section 12). Unset → slides, clip, translations and real names are hidden |
| `VIDEO_MODE` | `0` | `1` = recording the backup video: never serve or show the clip, the slides or any real name; everything else works |
| `DEMO_EXPECTED` | unset | Path to the local-only expected results file for the demo test |
| `REGRESSION_AUTOCOMMIT` | `0` | `1` = app commits new correction-memory rows and cluster files to the local git repo. Set to `1` only by the loop owner for the committed second-PR run |
| `LOOP_OPEN_PR` | `0` | `1` = push the branch and open a GitHub PR when a draft passes the gate. Set to `1` only for that same run |
| `LOOP_DRY` | `0` | `1` = do not start Codex; create the job and simulated attempts only (tests, UI work) |
| `CODEX_CMD` | `codex` | Command used to start Codex (the event's pinned CLI). On Windows use `cmd /c codex`. Prompts always go through stdin, never as arguments. Parsed into an argument list; the executable path may contain spaces only if quoted |
| `WORKTREE_ROOT` | `../_worktrees` | Draft-rule worktrees, resolved against the repo root: **outside** the repository. Paths may contain spaces |
| `RULE_KEEPER` | `hungkhuu404` | GitHub handle of the rule keeper (approves rules; one report from this handle quarantines a rule) |
| `T5_TAU` | `0.6` | Char 3-gram cosine threshold for clustering items without near hits, tuned on golden |
| `UI_PALETTE` | `oil` | `oil` (Dầu & Tín hiệu) or `graphite` (Than chì & Thép); switchable at runtime |
| `UI_STAGE` | `0` | `1` = stage tokens (slightly lighter background, text ×1.25); switchable at runtime |

**Stored versus per-command variables.** Only `TEAM_DIR` (team folder outside the repository, read by agents for status questions, never by the app), `LLM_BACKEND`, `CODEX_CMD` and `LLM_TIMEOUT_S` are stored per machine. Every other variable above is a mode flag: it is set for one command or one terminal session only. There is no `.env` file. `tests/conftest.py` pins every mode flag to its default for each test (`OFFLINE=1`, `LEDGER_DB` and `EVAL_DIR` to temp paths, `LLM_LOCAL_CACHE_DIR` to an empty temp folder, `PUBLIC_MODE=0`, `VIDEO_MODE=0`, `LOOP_DRY=0`, `REGRESSION_AUTOCOMMIT=0`, `LOOP_OPEN_PR=0`, `JUDGE_MODE=two`, models and efforts to the defaults above, `PRESENT_DIR` and `DEMO_EXPECTED` unset); a test that needs another value sets it itself.

### 3.1 Run configuration line

`python -m core.config` prints exactly one line, and the app prints the same line at start-up:

`cfg=<first 8 hex of sha256 of the canonical JSON of the fields below> head=<git short sha or "nogit"> codex=<first line of "<CODEX_CMD> --version" or "unavailable"> backend=<LLM_BACKEND> offline=<0|1> judge=<JUDGE_MODE> s1=<S1_MODEL>/<S1_EFFORT> judge_model=<JUDGE_MODEL>/<JUDGE_EFFORT> draft=<DRAFT_MODEL>/<DRAFT_EFFORT> timeout=<LLM_TIMEOUT_S> loop_dry=<0|1> autocommit=<0|1> open_pr=<0|1> public=<0|1> video=<0|1> present_dir=<set|unset> eval_dir=<EVAL_DIR> ledger=<LEDGER_DB> local_cache=<LLM_LOCAL_CACHE_DIR> cache_dir=<LLM_CACHE_DIR or unset> api_key=<set|unset>`

The hash covers every field except `head`, `codex`, `api_key`, `present_dir`, `eval_dir`, `ledger`, `local_cache` and `cache_dir`, so two machines in the same mode print the same `cfg`. The command never prints a key and never fails: a missing git or Codex prints `nogit` / `unavailable`. `GET /api/health` returns the same fields as JSON plus `stage` and `palette`.

**Event standard flags** (the table every machine compares its line with):

| Use | backend | offline | judge | loop_dry | autocommit | open_pr | public | video |
|---|---|---|---|---|---|---|---|---|
| Unit tests, CI | cache backend | 1 | two | 0 | 0 | 0 | 0 | 0 |
| Live run, shadow, draft-rule job (owner machine) | `codex` | 0 | two | 0 | 0 | 0 | 0 | 0 |
| Second-PR run (loop owner, one command) | `codex` | 0 | two | 0 | 1 | 1 | 0 | 0 |
| Stage and backup laptop | cache backend | 1 | two | per the stage runbook | 0 | 0 | 0 | 0 |
| Backup video | cache backend | 1 | two | 1 | 0 | 0 | 0 | 1 |

## 4. Enumerations

- **verdict:** `green`, `yellow`, `red`, `abstain`. Aggregation severity: `red` > `yellow` > `abstain` > `green`.
- **UI labels** (one strings file, English): green "Within file", yellow "Beyond file", red "Prohibited", abstain "Needs human". Each verdict always shows colour + shape + label: green = circle with tick; yellow = box with outward arrow; red = octagon with ×; abstain = dashed circle with "?" and dashed outline only (no fill).
- **axis:** `cong_dung`, `thanh_phan`, `chat_luong`, `doi_tuong`, `lieu_dung`, `xuat_xu`, `ho_so`, `khuyen_cao`, `vien_dan`, `so_sanh`, `tuyet_doi`, `gia`, `khuyen_mai`, `bao_hanh`.
- **axis_group** (groups of permitted items P): `cong_dung`, `thanh_phan`, `lieu_dung`, `doi_tuong`, `ho_so`, `xuat_xu`.
- **speaker_role:** `bac_si`, `duoc_si`, `nguoi_benh`, `kol`, `nguoi_ban`, `khong_ro`.
- **attribution** (of a turn): `press_attributed` (the press names this role as the speaker of this quote), `press_unknown` (the press does not say who said it), `constructed` (a line written by the team for the reenactment), `not_applicable` (eval cases, typed sentences, clip lines).
- **channel:** `livestream`, `written`, `video`.
- **category_norm:** `TPBVSK`, `thuc_pham_bo_sung`, `unknown`.
- **reason_code** (only on `abstain`):

| Code | Step | Meaning |
|---|---|---|
| `SKU_UNRESOLVED` | D | SKU not pinned, or not in the closed alias table, or no dossier file for it |
| `ASR_LOW` | D | Low-confidence ASR span (tier 2 only) |
| `SUBJECT_AMBIGUOUS` | D | The claim's subject is not the pinned product |
| `DOSSIER_EMPTY` | D | Whole P is empty at `as_of`, or the atom's axis slice is empty |
| `DOSSIER_FIELD_MISSING` | D | Atom axis is `doi_tuong` or `lieu_dung` and the dossier has that field with value null |
| `RULE_AMBIGUOUS` | B | Narrow negation question answered "unsure" (or not cached during replay) |
| `VOTES_SPLIT` | E | T1 and T2 disagree (combination table) |
| `LLM_NEI` | E | T1 returned `not_enough_info` |
| `LLM_TIMEOUT` | E | Live call over `LLM_TIMEOUT_S` |
| `LLM_OFF` | E | Backend `off`, `JUDGE_MODE=off`, or `OFFLINE=1` with a cache miss |
| `LAW_NOT_EFFECTIVE` | F | The family has legal-basis rows but none is effective at `as_of` |
| `RULE_QUARANTINED` | B | The rule that decided red is quarantined; shown as "⚪, previously 🔴" |

- **decided_by:** `allow_list`, `rule:<RULE_ID>`, `llm_votes`, `abstain:<REASON_CODE>`. Session flags use `rule:<RULE_ID>` of the flag rule.
- **session flag codes:** `NO_SELF_DECLARATION_AT_AS_OF` (rule `L1_PREDECL`), `NO_AD_CONFIRMATION` (rule `R06`), `RECALLED_AT_AS_OF` (rule `L1_RECALL`).
- **atom flags:** `RECALLED_AFTER` (verdict kept, dossier recalled after the broadcast date), `PREV_RED` (now abstain because the red rule is quarantined).
- **purpose:** `live`, `shadow`, `oracle`. **backend:** `api`, `codex`, `off`. **call kind:** `s1`, `t1`, `t2`, `negq`.
- **rule origin:** `law_compiled` (morning rule pack), `t5` (written by the adaptive loop). **rule status:** `proposed`, `active`, `quarantined`. **pack mode:** `live` (verdicts shown and stored; only approved rules), `measure` (gate, dry-run, oracle; may include one proposed candidate; results never stored as live verdicts).
- **legal-basis families:** `R04`, `R05`, `R06`, `R08`, `R09`, `L1_PREDECL`, `L1_RECALL`, `STANDARD` (the clause a green claim complies with), plus every T5 rule's own family.
- **job status:** `queued`, `running`, `passed`, `failed`, `error`. **gate result:** `pass`, `fail`.
- **error scale** (gate distance, per case or per atom): correct < `abstain` < lighter than label < heavier than label. For items with `abstain_expected = true`, `abstain` is correct and every colour is farther.

## 5. Records

**Session:** `session_id`, `sku_pin` (string or null), `as_of` (date), `channel`, `source` (shown on every card, for example "Reenactment from press and court quotes — not an original recording" or "Constructed sentence, entered by the team at HH:MM"), `turns[]`. Session ids are deterministic (`kera-reenact-<as_of>`, `case-<case_id>`, `text-<8 hex>`, `clip-a1`, `clip-a2`), and every ingest path is **ingest-if-absent**: ingesting an existing id returns it unchanged. Processing skips a session that already has verdicts for the live pack, unless `reprocess=True` (CLI `--reprocess`, implied by `--fresh`). Reprocessing first computes everything with the current code and prompts (no database transaction is open during LLM calls), then writes in one transaction: `ledger.replace_atoms` swaps the session's atoms and their search rows for the new extraction (an atom id may now hold a different span; atoms no longer produced leave the session view), and one new verdict per new atom is appended with `supersedes` = the latest verdict of the same atom key, if any (the history stays in `verdicts`).
**Turn:** `turn_id`, `speaker_label` (role label such as `[KOL 1]`, never a real name), `attribution`, `source_claim_ids[]`, `text` (NFC), `start_s`, `end_s` (floats, clip sessions only, else null). A real name may be displayed only when `attribution` is `press_attributed` (decision D23).
**Atom:** `atom_id`, `turn_id`, `span` (exact NFC substring of the turn text), `start`, `end` (character offsets into the turn text), `axis`, `speaker_role` (code-verified), `role_span` (substring of the current or previous turn, or null), `subject_is_product`, `near_hits[]`, `s1_call_id` (request sha256 or null).
**AtomView** (what rules and the near-hit detector receive): `text` (the NFC span), `key` (`textnorm.match_key` of the span), `axis`, `speaker_role`, `role_span`, `category_norm`, `as_of`, `turn_text` (NFC text of the whole turn, for clause context).
**DossierView** (what session rules receive): `sku_id`, `category_norm`, `ad_confirmation_status` (`present`, `absent`, `unknown` or null), `declaration_date` (date or null), `recall_date` (date or null).
**NearHit:** `pattern` (for example "phòng … bệnh"), `gap` (1 or 2), `law_anchor`, `law_span`, `span` (matched substring of the atom span).
**PermittedItem:** `permitted_id` (`<sku_id>:<axis_group>:<n>`), `axis_group`, `text` (NFC, rendered from the source field as defined in section 6), `confidence` (`high`/`medium`), `source_field` (JSON path in the source file).
**Context** (S0 output): `sku_id`, `category_norm`, `as_of`, `dossier_state` (Kera: `CHƯA_CÓ_HỒ_SƠ`, `ĐÃ_KÝ_CHƯA_NỘP`, `HIỆU_LỰC`, `ĐÃ_THU_HỒI`; evidence packs: `PRESENT`; no file: `NONE`), `P[]` (PermittedItem, never `low`), `reference_only[]` (low-confidence items, display only, never in prompts or matching), `null_fields[]` (axis groups present in the dossier with value null), `law_set[]` (anchor ids effective at `as_of`), `session_flags[]`, `dossier_hash` (sha256 of the canonical P only, not of recall state).
**SessionFlag:** `flag_code`, `rule_id`, `verdict` (`red`), `legal_basis[]`, `message` (English, no fine amounts).
**RuleHit:** `rule_id`, `family`, `matched` (substring of the atom span), `law_anchor`, `law_span`, `precedence` (int), `ambiguous_negation` (bool: a negation word is present but its scope is unclear; the router then asks `negq`).
**Verdict:** `verdict_id`, `atom_key`, `rule_pack_version`, `verdict`, `decided_by`, `reason_code`, `fired_rules[]`, `permitted_id`, `closest_permitted_id`, `evidence_span`, `law_span`, `numbers` (`{claim, dossier, consistent}` or null), `legal_basis[]` (anchor ids after F), `as_of`, `dossier_hash`, `llm_call_ids[]`, `supersedes` (verdict_id or null), `flags[]`, `qlnd_category`, `action`, `ts`.
**Rule module** (`rules/<RULE_ID>.py`): a docstring quoting the law verbatim; constants `RULE_ID`, `FAMILY`, `ORIGIN`, `ANCHORS`, `LAW_SPAN`, `PRECEDENCE` that agree with the meta file; a pure `check(atom_view)` returning RuleHit objects (session rules: `check_session(dossier_view, as_of)` returning SessionFlag objects).
**RuleMeta** (`rules/<RULE_ID>.meta.json`): `id`, `family`, `origin`, `article_id`, `anchors[]`, `law_span` (a phrase, or a list of phrases tried in order: step F keeps the first one found in a kept anchor), `source_case_ids[]`, `trigger` (one human-readable line), `precedence`, `attempts[]` (`{ts, gate_result, error_lines[], files_read[]}`), `revealed_case_ids[]`, `approved_by` (null until a human approves), `approved_at`, `status`.
**RulePack** (ledger row): `version`, `active[]`, `quarantined[]`, `change` (`init`, `merge`, `quarantine`, `unquarantine`), `rule_id`, `approved_by`, `ts`. A row is turned into a runtime pack with `rules.registry.load_pack(row.active, quarantined=row.quarantined, mode="live")`; `ledger.current_pack()` returns the row, never a runtime pack.
**LLMCall:** `request_sha256` (sha256 of the canonical JSON — sorted keys, separators `,` `:`, `ensure_ascii` false — of `{backend, model, effort, schema_name, schema, messages}`), `kind`, `purpose`, `backend`, `model`, `effort`, `usage` (`{input, output, cached, reasoning}` or null when unknown), `latency_ms`, `response` (parsed JSON or null when the call failed), `error` (null on success, else `{kind, exit_code, stderr_tail, detail}` with `kind` one of `off` (no call was made: backend `off`, or `OFFLINE=1` with a cache miss), `timeout`, `nonzero_exit`, `no_output`, `parse`, `schema`, `http`; step E maps `timeout` to abstain `LLM_TIMEOUT` and every other kind to `LLM_OFF`; `stderr_tail` is the last 2000 characters of stderr with anything that looks like a key masked), `ts`. A failed call is never written to any cache.
**Event:** `id` (integer, increasing), `ts`, `type`, `payload`.
**RegressionItem** (`EVAL_DIR/regressions.jsonl`): `item_id`, `level` (`case` or `atom`), `text`, `sku_id`, `as_of`, `label`, `rule_ids[]`, `labeler`, `ts`; extensions used by clustering: `kind` (`label` or `report_wrong`), `origin_verdict` (`{verdict, reason_code, decided_by}`), `near_hit_patterns[]`, `source_note`, `session_id`, `atom_id`.
**ClusterFile** (`EVAL_DIR/clusters/<name>.json`): `name`, `item_ids[]`, `texts[]`, `labels[]`, `cluster_hash`, `created_at`, `commit`, `runs[]` (`{job_id, rule_id, branch, started, attempts: [{attempt_no, gate_result, error_lines[]}], result, pr_url}`). Every draft-rule job writes or updates one (T5 clusters are named `t5-<first 8 hex of cluster_hash>`). The funnel can be computed from this file alone, on any machine. **An item listed in any cluster file is "used": T5 never triggers on it again.**
**Job:** `job_id`, `kind` (`draft_rule`), `rule_id` (allocated when the job is created; repeated runs on one cluster share it), `branch` (`rule/<rule_id>-<job_id>`: one branch and one worktree per job), `cluster_key`, `cluster_item_ids[]`, `cluster_hash`, `status`, `pid` (process id of the running Codex, null when not running), `attempts[]` (`{attempt_no, started, finished, gate_result, error_lines[], files_read[], log_path}`), `pr_url`, `dry` (true when created with `LOOP_DRY=1`), `config` (the `cfg` hash of section 3.1 at job start), `ts`. After at most 3 failing gate attempts the job is stored `failed` with its attempts and the cluster file run, and `job.finished` is emitted. "Draft failed" is a display state derived from the job (status `failed`), never a field of an item, a verdict or a label; items keep their original verdict. A cache miss inside the gate makes the job `error` (no retry, not counted in the funnel).
**ReplayResult:** per target (`session_id` or golden/eval `case_id`): `status` (`ok` or `not_evaluated` when an S1 or E cache row is missing), `verdicts[]`, `case_verdict` (eval cases), `negq_miss` (count).
**DryRun:** `rule_id`, `changed_cases`, `changed_atoms`, `changed_golden_outside_sources`, `flag_many` (true if more than 5 cases or another SKU), `observed_changes[]`.
**GateResult:** `passed`, `checks[]` (`{id, passed, detail}`), `error_lines[]`, `observed_changes[]`, `revealed_case_ids[]`.
**OracleReport** (`reports/oracle_<HHMM>.json`, aggregates only): `measured_at`, `n_cases`, `cache_miss`, `negq_miss`, `pack_v1`, `pack_v2`, `transitions` (counts by from→to class), `abstain_to_correct`, `correct_to_wrong`, `red_wrong` (`k`, `n`), `correct_refusals` (`x`, `n`), `who_decided_v1`, `who_decided_v2`, `mcnemar_p` (or null), `overlap_flags` (count only), `standin` (bool).
**WhoDecided:** `code` (allow_list + rule), `llm_votes`, `human` (abstain), `denominator`, `measured_at`, `source` (`ledger` or `oracle`).

## 6. Permitted items, axis slices and step D

**Where P comes from (nothing else may enter P):**

| Source | Group | Field |
|---|---|---|
| Evidence pack `data/evidence/<sku>.json` | `cong_dung` | `permitted_claims[].text` |
| | `thanh_phan` | `ingredients_declared[].text` |
| | `lieu_dung` | `usage_declared[].text`, `usage[].text` |
| | `doi_tuong`, `ho_so`, `xuat_xu` | never (always empty for evidence packs) |
| Kera dossier `data/demo/kera/dossier.json`, state `HIỆU_LỰC` only | `ho_so` | `product.name_on_label.value`, `product.category.value`, `product.pack_size.value` |
| | `thanh_phan` | `ingredients_declared.summary.value`; each element of `ingredients_declared.ten_vegetable_powders.value`; each `ingredients_declared.other_declared_ingredients[i].value` not `low`; each `nutrition_declared.rows[i]` rendered as "`<name>`: `<value>` / `<nutrition_declared.per>`" |
| | `cong_dung` | none (`label_claims.function_claim_in_dossier.value` is null) |
| | `lieu_dung`, `doi_tuong` | none; `label_claims.usage_instructions` and `label_claims.target_users` are null → listed in `null_fields` |

Items with `confidence = low` go to `reference_only`. Answer-leaking fields never enter P or any prompt: `gaps`, `regulator_warnings`, `note`, `source_detail`, `label_claims_not_in_declaration`, and in the Kera dossier every regulator statement, `self_declaration` (including `attached_test_report`), `actual_vs_declared`, `not_declared_but_present`, label slogan, icons, logos, `box_bullets`, `per_item_percent_on_label`, `validity`, `related_regulatory_events`, `sales_window`, and every `note`, `sources`, `method` or `excerpt` field.

**Axis slices:**

| Atom axis | Slice of P (axis groups) |
|---|---|
| `thanh_phan` | `thanh_phan` |
| `lieu_dung` | `lieu_dung` |
| `doi_tuong` | `doi_tuong` |
| `ho_so` | `ho_so` |
| `xuat_xu` | `xuat_xu` |
| `chat_luong` | `cong_dung` + `thanh_phan` + `ho_so` |
| `cong_dung`, `so_sanh`, `tuyet_doi`, `vien_dan`, `khuyen_cao`, `bao_hanh` | `cong_dung` + `thanh_phan` |
| `gia`, `khuyen_mai` | dropped at S1; if they reach S2, empty slice |

**Step D**, first match wins: `SKU_UNRESOLVED` → whole P empty → `DOSSIER_EMPTY` → `ASR_LOW` → `SUBJECT_AMBIGUOUS` → axis is `doi_tuong` or `lieu_dung` and that group is in `null_fields` → `DOSSIER_FIELD_MISSING` → slice empty → `DOSSIER_EMPTY`. (Whole-P-empty comes early so that a session with no dossier, such as Kera on 12/12, gives `DOSSIER_EMPTY` for every atom whatever S1 says about the subject.) Step E sees only the slice through `ctx.prompt_view(groups)`.

## 7. Step E combination table

T2 "found" = at least one P_i with `entails = true`; "none" = no P_i.

| T1 | T2 | Result |
|---|---|---|
| `supported` | found | green; quote = P_i by id; highlight `evidence_span` (code-checked); legal basis family `STANDARD` (the same family as a green from step A) |
| `beyond_scope` or `contradicts` | none | yellow; family R05; basis from `rules/law_basis.json` at `as_of` |
| `supported` | none | abstain `VOTES_SPLIT` |
| `beyond_scope` or `contradicts` | found | abstain `VOTES_SPLIT` |
| `not_enough_info` | any | abstain `LLM_NEI` |

## 8. Module interfaces (names are fixed; signatures are prose)

- `core.config.settings` (the one shared settings object, built from environment variables at import); `core.config.set_override(name, value)` (validated override for CLIs and tests, updates `settings` in place; unknown names raise); `core.config.config_line()` → the run configuration line of section 3.1; `core.config.config_fields()` → the same fields as a dict (used by `/api/health`, snapshots and the app start-up line); `python -m core.config` prints `config_line()`.
- `textnorm.nfc(s)`; `textnorm.match_key(s)` returns the key and an offset map back to `s`; `textnorm.locate(span, text)`; `textnorm.fold(s)`; `textnorm.law_normalize(s)` (exactly `data/law/normalize.md`); `textnorm.syllables(key)`; `textnorm.TEENCODE_VERSION`.
- `rules.roles.ROLE_WORDS` (role → words that prove a speaker role; used by `pipeline.extract` to verify `role_span`).
- `pipeline.context.build_context(sku_id, as_of, pack=None)` → Context (session flags from `pack`, default the live pack); `ctx.prompt_view(groups)`; `pipeline.context.resolve_sku(text)`.
- `pipeline.ingest.from_reenactment(path, sku_id, as_of)`, `from_eval_case(case)`, `from_text(sku_id, as_of, text, source)` → Session. `pipeline.ingest_clip.from_clip_transcript(path, sku_id, as_of, source)` → Session (owner Phúc).
- `pipeline.extract.extract_atoms(session, turn, prev_turn, ctx, purpose="live", cache_read=True)` → atoms; `pipeline.extract.extract_batch(...)` runs it for many turns through `map_calls`. `purpose` is passed to the LLM layer (it decides where cache rows go and where calls are logged). `ctx` is used only for the product display name: the S1 request never contains `as_of`, P or anything date-dependent.
- `rules.registry.load_pack(rule_ids, quarantined=(), mode="live", candidate_dir=None)` → runtime pack with `check_atom(atom_view)` → list of RuleHit, `check_session(dossier_view, as_of)` → list of SessionFlag, and `quarantined_hits(atom_view)` (hits of quarantined rules, for `RULE_QUARANTINED`). Mode `live` refuses any rule whose meta has `approved_by` null. Mode `measure` may add proposed candidates (from `candidate_dir` or from the tree) for the gate, the dry-run and the oracle v2; its results are never stored as live verdicts. `rules.registry.all_rules()` → rules with meta.
- `rules.registry.all_rules()` → list of RuleInfo `{rule_id, module, meta}` (meta = RuleMeta), sorted by `rule_id`, every rule file in `rules/` including proposed ones; `ledger.init_db`, the gate, step F and `/api/rules` read rules only through it.
- `rules.nearhit.detect(atom_view)` → list of NearHit (never a colour).
- `pipeline.router.route_atom(atom, ctx, pack, purpose, emit=None)` → Verdict (A, B, C skipped in tier 1, D, E, then F). `emit` is the event callback `emit(type, payload)` (synchronous, returns nothing; the live runner passes one that calls `ledger.append_event`); `None` means no events (eval, gate, oracle).
- `pipeline.guardrail.finalize(draft, ctx, rule_pack_version)` → Verdict (F; the caller supplies the pack version); `pipeline.guardrail.finalize_flag(flag, ctx)`; `pipeline.guardrail.display_text(anchor_id)` → text with every fine amount removed; `pipeline.guardrail.is_effective(anchor_id, as_of)` (the only place that reads law dates).
- `pipeline.aggregate.turn_verdict(verdicts)`, `case_verdict(turn_verdicts, session_flags)`, `case_decided_by(...)`, `who_decided(verdicts)`.
- `pipeline.judge.judge(atom, slice_items, ctx, purpose, cache_read=True)` → draft (step E).
- `pipeline.llm.call(kind, purpose, model, effort, schema_name, messages, timeout_s, cache_read=True)` → result with `request_sha256`, parsed response, usage, latency, `cache_hit`, `error`. `acall` and `map_calls` for async and batches. `pipeline.llm.run_scope(offline=False, measuring=False)` is a context manager that forces offline and/or marks a measuring run for the current thread or task only (never a global override); `replay`, `run_cases`, `judge-repeat`, the gate and the demo test use it. `pipeline.llm.call` refuses to run inside a running event loop (callers in the app use `asyncio.to_thread`). JSON schemas live in `pipeline/schemas/<schema_name>.json`. Cache layout: one file per row, `<dir>/<sha[:2]>/<sha>.json` holding the full LLMCall, written atomically; `pipeline.llm.cache_path(directory, request_sha256)` returns that path (the loop uses it to copy rows into `cache/llm/`). Cache lookup order: `LLM_CACHE_DIR` if set, then `cache/llm/`, then `LLM_LOCAL_CACHE_DIR`; new rows go to `LLM_CACHE_DIR` if set, else to `LLM_LOCAL_CACHE_DIR` (purpose `live`) or `cache/llm/` (purpose `shadow`). Purpose `shadow` **promotes**: a hit found in `LLM_LOCAL_CACHE_DIR` is copied into `cache/llm/`, so rows produced live become committable. **Where each call is logged:** purpose `live` → the ledger table `llm_calls`; purpose `shadow` and every measuring run (gate, `judge-repeat`, stand-in scoring, demo test) → `.local/llm_calls.jsonl`, never the ledger; purpose `oracle` → a log file under `LLM_CACHE_DIR` only, never inside the repository. Cache hits are logged the same way. `python -m pipeline.llm diagnose --kind <s1|t1>` sends one fixed public request (a built-in synthetic sentence, never golden or held-out text) with the current backend, model and effort and prints exit code, error kind, stderr tail, output-file presence, parse result and request hash (no key, no cache write, no ledger write).
- `pipeline.runner.process_session(session_id, purpose, reprocess=False)` (skips a session already processed for the live pack unless `reprocess`); `pipeline.runner.replay(targets, pack=None, persist=False, purpose="live", pack_version=None)` → list of ReplayResult; `targets` are session ids or eval cases; cache only; `pack` is a runtime pack (None = the live pack from the ledger); `purpose="oracle"` also guarantees nothing is written or logged inside the repository; `pipeline.runner.run_cases(cases, purpose, pack, pack_version=None)` → ReplayResult per case; in memory only (no ledger, no search index, no events). Every LLM-facing function forwards `purpose` down to `pipeline.llm.call`.
- Runner details: `replay` returns one ReplayResult per target, in input order; a RegressionItem is replayed as a case (`case_id` = `item_id`, with its `sku_id`, `as_of`, `text`). `replay` and `run_cases` pass their pack to `pipeline.context.build_context` and take `rule_pack_version` from the caller (live runs: `ledger.current_pack().version`), so measure and oracle runs never open `LEDGER_DB`. A session counts as processed when it has any verdict row; a new pack reaches stored sessions only through `backfill`.
- `python -m pipeline.cli` subcommands: `kera --as-of <date> [--reprocess]`, `process --session <id> --purpose live [--reprocess]`, `case --id <case_id>`, `sentences --sku <id> --as-of <date> --file <utf-8 file, one sentence per line> --source <text>` (ingests and processes typed sentences; prints for each sentence its `session_id` and, for each atom, `atom_key`, verdict, reason code and near-hit patterns, so a person can label by `atom_key`), `shadow [--s1-only] [--golden] [--kera] [--regressions] [--sentences]` (no flag = all; `--sentences` reads `tests/demo/pr1_sentences.json` = `{sku_id, as_of, source, sentences[]}`; shadow builds the pack `v1` from rule metadata and never opens `LEDGER_DB`; refuses `clip-*` sessions and never reads `PRESENT_DIR`), `judge-repeat --session <id> --turn <turn_id> --n <n>` (sequential, no ledger writes). `kera`, `process` and `sentences` create the ledger with `ledger.init_db` when the file is missing. Each prints one line per turn and a summary line (calls, cache hits, misses, errors).
- `pipeline.dossier_events.auto_run(session_id)`: starts `pipeline.runner.process_session(session_id, "live")` in a background thread and returns at once. The API appends `session.ingested` when it creates a session, then calls it.
- `pipeline.dossier_events.apply_recall(sku_id, recall_date, source)` → per-session flagged counts; `propose_dossier_update(sku_id, change)` → pending item; `approve_pending(pending_id, approved_by)`; `quarantine(rule_id, reason)`; `backfill(pack)`.
- `pipeline.rawpath.check_sentence(sku_id, as_of, text)` → Verdict from code only (A, disclaimer, negation, B, D); `pipeline.rawpath.preflight(sku_id, as_of, text, full)`.
- `ledger` functions: `init_db(path, exclude_rules=())` (first rule pack = approved active rules minus `exclude_rules`), `transaction`, `add_session(session)` (ingest-if-absent: stores the session, its turns and their search rows; returns the stored session), `get_session(session_id)` (the session with turns, atoms, latest verdicts and flags), `add_atoms(session_id, atoms)` (stores atoms and their search rows), `append_verdicts(verdicts)` → list of assigned `verdict_id` in input order, `latest_verdicts`, `append_event`, `events_since`, `index_turn`, `index_atom`, `replace_atoms(session_id, atoms)` (reprocessing only: removes the session's atom rows and atom search rows, then indexes the new atoms; called inside `transaction`), `search`, `record_llm_call`, `create_job(job)` → `job_id`, `update_job(job_id, **changes)`, `current_pack`, `create_pack_version(active, quarantined, change, rule_id=None, approved_by=None)` → the new RulePack row (version = previous + 1), `add_session_flags`, `add_pending`.
- Ledger read functions: `get_job(job_id)`, `list_jobs(rule_id=None, status=None)`, `get_verdict(verdict_id)`, `add_label(label)` (label row: `target`, `kind` (`label` or `report_wrong`), `label`, `rule_id`, `labeler`, `verdict_id`, `ts`), `list_labels(session_id=None)`, `get_pending(pending_id)`, `list_pending(status=None)`, `update_pending(pending_id, **changes)`, `effective_verdicts(session_id, pack_version=None)`: one verdict per atom, its newest row with `rule_pack_version` ≤ the pack version (default: current). Backfill appends only the verdicts that changed, so every screen shows effective verdicts, and `get_session` returns them.
- `loop.labels.record_label(target, label, rule_id, labeler, auto_trigger=True)`, `loop.labels.record_report_wrong(verdict_id, reporter, correct_label=None)`; `loop.t5.maybe_trigger()` (never uses items listed in any cluster file); `loop.draft.run_job(job_id)`, `loop.draft.funnel(cluster_hash)`; `loop.merge.dry_run(rule_id)`; `loop.merge.merge(rule_id, approved_by)` (needs a passing gate result from a ledger job, or else from the rule meta `attempts`, or else from the cluster file `runs`).
- `api` startup helper `ensure_clip_session(reprocess=False)`: ingest-if-absent of `clip-a1` and `clip-a2` (when their files exist) when `PRESENT_DIR` is set, then process them; the only clip ingestion path (the app start-up, `POST /sessions` with kind `clip`, and `scripts.demo_state prepare` all call it). Inside the app it runs in a worker thread (`asyncio.to_thread`), never inside the running event loop; it never calls `asyncio.run` from a coroutine. With `OFFLINE=1` and no cached rows the clip atoms are abstain `LLM_OFF` (never an error).
- `gate.run_gate(rule_id, candidate_dir, log=None, sources=None)` → GateResult; `gate.score` CLI.
- `gate.score --pack v1|current`: `v1` builds the pack from rule metadata only (approved rules with origin `law_compiled`, every T5 rule off) and never opens `LEDGER_DB`; `--purpose oracle` and `--standin-every` accept only `v1`. `current`: the CLI calls `ledger.current_pack()` before the runner and passes pack and version into the runner (the runner never opens the ledger); it requires that the file `LEDGER_DB` already exists and otherwise exits with an error without creating the file. There is no `<version>` value.
- `gate.score --write-baseline` writes `gate/baseline.json` (case ids wrong at tier 1). The first baseline of the day is written with `--mode replay --pack v1 --write-baseline`, `OFFLINE=1` and `LLM_BACKEND` = the cache backend, after the full shadow is on `main`; it needs no ledger, and must report 0 `not_evaluated` and 0 `negq_miss`.
- Gate `--ci`: the BEFORE pack is built from rule metadata with exactly the conditions `ledger.init_db` uses for the first pack (approved, active, not excluded), without a ledger; scope comes from the PR diff against `--base`; checks 7 and 8 are skipped.
- Gate subprocesses (check 4 own tests) run with a minimal environment: `OFFLINE=1`, every mode flag of section 3 at its default, `LLM_BACKEND` copied, no key, `PYTHONDONTWRITEBYTECODE=1`, plus only the OS variables Python needs to start (`PATH`, `SYSTEMROOT`, `COMSPEC`, `TEMP`, `TMP`, `HOME`, `USERPROFILE`); never the caller's full environment.
- Gate on a machine (not `--ci`): the BEFORE pack is `ledger.current_pack()`; a missing `LEDGER_DB` file is an error (the file is never created).
- Gate module names: `gate.logscan.scan_log(log, candidate_dir, rule_id)` → (files_read, error_lines); `gate.behaviour.run_behaviour(rule_id, candidate_dir, sources=None, ci=False)`; the purity allow-list and forbidden names are the module constants `gate.run.PURITY_IMPORTS` and `gate.run.PURITY_FORBIDDEN` (the section 14 block prints them). Error lines start with `<RULE_ID> check <n>: `. A missing cache row is the line `cache miss: run python -m pipeline.cli shadow` (shared by the gate and the loop). CLI flags: `gate.run --rule --candidate-dir [--log] [--sources] [--ci --base <ref>]`; `gate.score [--mode replay|llm] [--purpose shadow|oracle] [--cases <file>] [--standin-every <n>] [--cache-dir <dir>] [--pack v1|current] (default v1) [--aggregate-only] [--write-baseline] [--out <file>]` (`--out` writes the aggregates as an OracleReport; for purpose `oracle` the file must be outside the repository). The oracle verifies case files against `data/cases/FREEZE.json`.
- Loop names: `loop.draft.ALLOWED_COMMANDS` (the section 14 block, one constant); `loop.t5.allocate_rule_id(family)`.
- **The only commands that touch the sealed set:** first `python -m gate.score --mode llm --purpose oracle …` (one LLM pass), then `python -m gate.oracle replay …` (code only, exactly one `--v2-rules` id). Both write their aggregate report as an OracleReport file the rule keeper copies into `reports/` (`oracle_<HHMM>.json`). Nothing else ever reads the sealed set.
- `api.app.create_app(public=False)`.

## 9. Ledger tables (SQLite, WAL)

`sessions`, `turns`, `atoms` (replaced per session only by reprocessing), `verdicts` (append-only; newest row per atom and pack wins; `supersedes` links), `session_flags`, `events` (append-only), `search_fts` (FTS5 with columns `x` = lowercase NFC text and `xf` = folded text, plus unindexed ids), `llm_calls`, `jobs`, `job_attempts`, `rule_packs`, `labels`, `pending_dossier`. Backfill, quarantine and recall each write in one transaction.

## 10. HTTP API (JSON; all under `/api`)

| Method and path | Purpose |
|---|---|
| `GET /health` | Liveness, pack version, stage, palette, and every field of the run configuration line (section 3.1) |
| `GET /sessions`, `GET /sessions/{id}` | List; one session with turns, atoms, latest verdicts, flags |
| `POST /sessions` | Ingest (`kind`: `reenactment`, `text`, `clip`) |
| `POST /sessions/{id}/process` | Run the pipeline |
| `POST /preflight` | `{sku_id, as_of, text}` → raw-path verdict at once; S1 path follows as events (public mode: raw path only) |
| `GET /events?since=` | Poll (public link, every 300 ms) |
| `GET /events/stream?since=` | SSE (laptop) |
| `GET /search?q=&session_id=` | Lexical search on `x` then `xf`; bm25; tie-break score desc, id asc |
| `GET /queue` | Abstain items sorted by risk, with chips |
| `POST /labels` | `{target, label, rule_id, labeler, auto_trigger}`; `auto_trigger` defaults to true (false = record only, no T5 check) |
| `POST /report-wrong` | `{verdict_id, reporter, correct_label}` (`correct_label` optional) |
| `GET /jobs`, `GET /jobs/{id}` | Draft-rule jobs with attempts, error lines, files read, funnel |
| `GET /rules`, `GET /rules/{id}` | Rule list; provenance card (attempts from the meta file and the cluster file when no local job exists) |
| `GET /rules/{id}/dryrun` | "N old verdicts will change" |
| `POST /rules/{id}/merge` | `{approved_by}` → new pack version + backfill |
| `POST /dossier-events/recall` | `{sku_id, recall_date, source}` |
| `POST /dossier-events/update` | Dossier change; greener results go to pending |
| `POST /pending/{id}/approve` | `{approved_by}` |
| `GET /who-decided` | Live counts plus the latest oracle report |
| `GET /present/manifest` | Slides list, clip/subtitle/translation/names availability, `present.json` (laptop only) |

## 11. Event types

| Type | Payload |
|---|---|
| `session.ingested` | `session_id, sku_pin, as_of, channel, n_turns` |
| `step` | `session_id, turn_id, atom_id, step` (`S0`,`S1`,`A`,`B`,`C`,`D`,`E`,`F`,`S3`), `outcome, ms` |
| `verdict` | `verdict_id, session_id, turn_id, atom_id, verdict, decided_by, reason_code` |
| `turn.verdict` | `session_id, turn_id, verdict` |
| `session.flag` | `session_id, flag_code, rule_id, legal_basis` |
| `queue.changed` | `{}` |
| `label.recorded` | `item_id, label, rule_id, labeler, kind, commit` |
| `t5.cluster` | `cluster_key, size, item_ids` |
| `job.started` | `job_id, rule_id, cluster_key` |
| `job.attempt` | `job_id, attempt_no, gate_result, error_lines, files_read` |
| `job.finished` | `job_id, status, pr_url` |
| `rule.merged` | `rule_id, version, approved_by` |
| `backfill.done` | `version, changed_cases, changed_atoms` |
| `recall.applied` | `sku_id, recall_date, sessions` (list of `{session_id, flagged_atoms}`; the UI banner shows the count of the session on screen) |
| `rule.quarantined` | `rule_id, reason, affected` |
| `dossier.pending` | `sku_id, pending_id, greener_count` |

## 12. Presenter assets (local only) and hotkeys

`PRESENT_DIR` holds files that never enter git: `slides/*.png` (sorted by name), `clip/a1.mp4`, `clip/a1.transcript.json` (lines with `start`, `end`, `text`), `clip/a1.subs.json` (lines with `start`, `end`, `vi`, `en`), `clip/a1.meta.json` (`source_line`, `speaker_line`, `as_of_note`), the same four files for the backup clip `a2`, `names.json` (role label → display name), `translations.json` (NFC Vietnamese text → English line, human-checked; used for the English line under every on-screen quote), `present.json` (`pr1_third_target`: the atom key labelled by key `L`; `labeler`: GitHub handle written as labeler; `approver`: handle used by the Merge button). The API serves them only when `PRESENT_DIR` is set and `PUBLIC_MODE=0`; with `VIDEO_MODE=1` it serves no clip, no slide and no name.

`display_speaker` (computed by the API, never by the UI): `press_attributed` → the real name from `names.json` only when all four hold: `PRESENT_DIR` is set, `PUBLIC_MODE=0`, `VIDEO_MODE=0` and the request carries the query parameter `presenter=1` (sent only by presenter mode); otherwise the role label; `press_unknown` → "Speaker unknown"; `constructed` → "Reenactment line (team-written) · <role label>". Clip: the attribution line under the clip is `speaker_line` from `clip/<a>.meta.json`, served under the same four conditions (else the fields are absent) by `GET /api/present/manifest?presenter=1` as `clips.<a>.speaker_line` and `clips.<a>.source_line`, and by `GET /api/sessions/clip-<a>?presenter=1` as top-level `speaker_line` and `source_line`; the UI shows it verbatim. Clip turns carry `speaker_label` `[unidentified]` and attribution `not_applicable`; the transcript lines of the clip panel show "Speaker: unidentified". The repository never contains the text of `speaker_line`, `source_line` or `names.json`. The demo test and the API tests use the same four conditions.

**Stage mode corner.** With `UI_STAGE=1` the UI shows a small corner line from `/api/health`: backend, `offline`, and "DRY RUN" in clear letters whenever `loop_dry=1`. A Review card of a job with `dry=true` also shows "DRY RUN".

**PR1 target selection** (demo test and `prepare`): keep every atom of the three prepared sentences; for each sentence pick the unique atom that has the shared near-hit pattern and verdict abstain `DOSSIER_EMPTY`; if none or more than one atom matches, stop with an error naming the sentence. Never take `atoms[0]`; never change the sentence, the prompt or the cache to make it fit.

| Key | Action |
|---|---|
| `→` / `←` | Next / previous slide; `→` on the last slide opens the demo |
| `L` | Silently label the third prepared abstain item (starts PR1). The slide does not change |
| `1` … `6` | Jump to scene: 1 opening clip, 2 Kera 08/01, 3 12/12, 4 recall 25/3, 5 Review (PR1, PR2), 6 Merge PR2 |
| `Shift+1` | Opening scene with the backup clip `a2` |
| `B` | Back to the previous scene |
| `P` / `S` / `F` | Palette switch / stage mode / fullscreen |
| `Esc` | Leave presenter mode |

Scene keys are ignored while a text input has focus.

**Serving assets.** The app is started with `python -m api [--public] [--port <n>]` (default 8000). Presenter files are served under `/present/` only from an allow-list (`slides/*.png`, `clip/<a>.mp4`, `clip/<a>.subs.json`); `names.json`, `*.meta.json`, `*.transcript.json`, `translations.json` and `present.json` are never served as files (the API reads them). `GET /api/present/manifest` lists slides, clip/subtitle availability, the translation table entries needed on screen, and the `present.json` keys. Demo-test fixture `tests/demo/pr1_sentences.json` (owner Hưng) lists the three PR1 sentences; `scripts.demo_state prepare` reads it.

## 13. Fixtures (`fixtures/`, synthetic only)

Use an invented SKU `demo_sku` with 3 permitted items (one per group `cong_dung`, `thanh_phan`, `ho_so`). Provide: `session_fake.json` (3 turns, one per attribution value except `not_applicable`), `context_fake.json`, `atoms_fake.json` (5 atoms: one green via A, one red via B, one yellow via E, one abstain `DOSSIER_FIELD_MISSING`, one abstain `LLM_NEI`), `verdicts_fake.json`, `events_fake.jsonl` (one full pipeline of `step` events for one atom), `rule_meta_fake.json`, `job_fake.json` (two attempts: fail then pass, with one error line), `cluster_fake.json`. No real person names, no golden or Kera sentences in fixtures.

## 14. Draft-rule agent: allowed commands (single source)

The `draft-rule` skill (section "Allowed commands"), the first lines of the job prompt that `loop/draft.py` sends on stdin, and the top of `loop_input/retry.md` all contain the same block, generated from one constant in `loop/draft.py` (a test asserts the three are identical). Gate check 7 (`gate/logscan.py`) accepts exactly this grammar and nothing wider:

- One single-line command per shell invocation; optionally wrapped once as `powershell -Command '<cmd>'`, `pwsh -Command`, `cmd /c`, `bash -lc`, `bash -c`, `zsh -lc`, `zsh -c` or `sh -c` (the wrapper may be given by full path, for example `/bin/zsh -lc`, as Codex does on macOS). Several commands joined by `;`, `&&`, `||` or `|` are checked one by one. No `$(`, backticks, `${`, newlines, heredocs, redirection (`<`, `>`, `>>`), environment-variable prefixes (`OFFLINE=1 …`, `$env:…`), `env`, `set`, `export`, `cd`, package installers, the gate, any `python` command other than the test command (no `python -c`), `git` other than `status` and `diff`.
- Reads: `cat`, `type`, `head`, `tail`, `sed -n`, `rg`, `grep`, `ls`, `pwd` on literal allowed paths (on Windows PowerShell `rg`, `grep`, `sed`, `head` may not exist: the block tells the agent to read files only with `Get-Content` there and never to use `Select-String` or `Get-ChildItem -Recurse`); `Get-Content <path>`, `Get-Content -Path <path>` or `Get-Content -LiteralPath <path>`, each with only these harmless read-only options in any order: `-Raw`, `-Encoding UTF8` (also `utf8`), `-TotalCount <n>`. Every path is checked against the allowed set of AGENTS.md invariant 12 exactly as before; paths outside it, `..`, `~`, `$` and absolute paths outside the candidate are rejected.
- Tests: exactly `python -m pytest -q tests/test_<rule_id>.py` or `pytest -q tests/test_<rule_id>.py` (same harmless options as before), never with a prefix.
- Files are created and edited only with the file-editing tool, never with shell or Python write commands.
- Rule purity: no `re.compile` (use `re.search`, `re.finditer`, `re.fullmatch` with pattern strings); the rejected-name list of check 1 is printed in the block.
- The worktree has no `docs/`: the agent never reads `docs/contracts.md`.

A test feeds `gate/logscan.py` a sanitised Codex log that follows the block on Windows (`powershell -Command 'Get-Content -Raw -Encoding UTF8 .agents/skills/draft-rule/SKILL.md; Get-Content -Raw -Encoding UTF8 loop_input/task.json'`, the own test command, file edits) and one on macOS (`cat`), and both pass; the same logs with one forbidden path, an `OFFLINE=1` prefix, `env` or a heredoc fail with one error line each.

## 15. Public repository hygiene

`tests/test_no_package_traces.py` (owner Hưng) fails when a tracked path or the text of a tracked file (other than `AGENTS.md` and `docs/contracts.md`) matches the team's list of planning artefacts and private markers: planning and timing files, team coordination file names and folders, team chat markers, local machine paths (drive-letter team folders, home folders), session logs of coding agents, API-key patterns, `.env`, archives, videos and `names.json`. The list lives only inside the test, built from string fragments so the test does not match itself. `.gitignore` covers `.local/`, `.env`, `*.zip`, `*.mp4` and `present/`.

## 16. Demo state (`scripts.demo_state`)

- `snapshot --name <n>` stores the ledger, `EVAL_DIR` files, rule meta files, the list of worktrees and `rule/*` branches, `present.json` and the run configuration line (section 3.1) of the machine that took it.
- `restore --name <n>` refuses while the app port is open or a Codex job of this checkout is running, and refuses when the current run configuration differs from the snapshot in `backend`, `offline`, `judge`, the models or `public`/`video` (it prints both lines). The app in stage mode prints a red banner on the same mismatch.
- `prepare [--exclude-rule <RULE_ID>]` stops only jobs whose pid file is under this checkout's `.local/jobs/`, never processes of another checkout; then ingests the clips with `ensure_clip_session`, labels the two prepared PR1 sentences (PR1 target selection, section 12) and snapshots `stage`.
- `prepare` prints the number of clip atoms that are abstain `LLM_OFF`; the stage step accepts the snapshot only when that number is 0.
- `export --name <n> --to <dir>` and `import --from <dir>` move the snapshot, `present.json` and the clip cache rows of section 3 (`LLM_LOCAL_CACHE_DIR` rows for `clip-a1`, `clip-a2`) without overwriting existing files (exception: `import` updates only the key `pr1_third_target` of the target's `present.json`; a snapshot with the same name and different content is refused); slides, clips, subtitles and names are copied separately by hand. `restore` never stops a process itself: with a running job it refuses and prints the pid; only `prepare` stops jobs of its own checkout.
- Two apps on one machine must use two separate checkouts (separate `.local/`, ledger, `EVAL_DIR`, jobs, snapshots, `PRESENT_DIR` copy).

