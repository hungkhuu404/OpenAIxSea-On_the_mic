# Grounded Claim Ledger

**Draft for owner review, not a final submission.** Evidence snapshot: `b639793`. Implementation scope below comes from merged history and the repository contracts; it does not certify a successful demonstration or a measured improvement.

## Problem and standard

A livestream statement must be checked against the dossier of the exact product and the law applicable on the broadcast date. A document that exists today may not have existed when the claim was made; a later recall must not silently rewrite that earlier decision. Grounded Claim Ledger applies the standard **“permitted by the dossier at the broadcast date”**, rather than treating a plausible statement as established evidence. [Project description](../README.md), [invariants](../AGENTS.md#hard-invariants-tests-enforce-them-never-break-them).

TODO (rule keeper): verify and cite the statutory obligation for livestream platforms to establish their own control measures, and the narrower statement that the law does not prescribe a method for comparing spoken claims with the product dossier. The legal-source register identifies the relevant legislation but does not establish that interpretation by itself: [law sources](../data/law/SOURCES.md).

## Implemented path and evidence

The contracted pipeline is **S0 context → S1 atomic claims → S2 router A–F → S3 aggregation → append-only ledger**. Merged implementation records cover each of these components:

| Component | Repository evidence |
|---|---|
| Product/date context and restricted dossier views | [Context implementation](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/9) |
| Source-attributed ingestion and atomic extraction | [Ingestion and extraction](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/14), [speaker-proof correction](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/17) |
| Code rules, routing, replay and CLI | [Rule registry](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/6), [session rules](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/5), [router](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/19), [routing correction](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/25), [flag-state correction](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/27) |
| Two-vote step and evidence comparison | [Judge implementation](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/23) |
| Legal-evidence guardrail and aggregation | [Guardrail and aggregation](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/16), [runtime metadata correction](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/21) |
| Durable verdicts, events, search and session display | [Ledger and API](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/11), [ledger reads](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/20), [flag replacement](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/26), [UI](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/13), [local public-mode interface](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/24) |
| Scorer, structural gate, log validation and candidate behaviour checks | [Structural gate and scorer](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/22), [behavioural gate](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/18) |
| Human labels, correction memory and clustering | [Label and clustering implementation](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic/pull/28) |

Code decides first. Exact permitted matches can be green; prohibited claims are red only through code rules or session flags. When code cannot decide, the judge combines two separately requested votes. Agreement can support green or yellow; disagreement or insufficient evidence produces abstain, displayed as “Needs human.” The contract requires source-bound quotations and highlights, permitted-item identifiers for dossier evidence, and law anchors filtered by effective date. A match is not a general endorsement of the product. [Decision rules and record contracts](contracts.md#4-enumerations), [pipeline interfaces](contracts.md#8-module-interfaces-names-are-fixed-signatures-are-prose).

## Build direction: Autonomous & Adaptive; Deep Domain

The **Deep Domain** foundation combines effective-dated law anchors, category-specific legal bases, product records and dossier states by date. The source registers distinguish official records, press reporting and lower-confidence material. Lower-confidence references must not enter the permitted-claim set. [Law basis](../AGENTS.md#law-basis-table-ruleslaw_basisjson), [registry sources](../data/registry/SOURCES.md), [case and evidence sources](../data/cases/SOURCES.md), [dossier contract](contracts.md#6-permitted-items-axis-slices-and-step-d).

The **Autonomous & Adaptive** direction is a contracted extension: abstain queue → human label → clustering → Codex draft-rule job in a sparse worktree without evaluation sets → external gate → sealed-set measurement → human merge. Recalls are specified to apply automatically. Asymmetric autonomy means that unattended actions may make the system more cautious; changes that expand its authority require human approval. Near-hit similarity suggests review candidates and never chooses a verdict. [Adaptive-loop interfaces](contracts.md#8-module-interfaces-names-are-fixed-signatures-are-prose), [autonomy invariants](../AGENTS.md#hard-invariants-tests-enforce-them-never-break-them).

TODO (rule keeper): verify implementation and integration evidence for the gate, adaptive jobs, draft-rule skill, recall handling and human merge before presenting this extension as a completed loop. Their contracts are not evidence of a successful run.

## Codex and the build record

The documented workflow supplies Codex with `AGENTS.md` and `docs/contracts.md`, uses separate task worktrees, and requires tests and review before merge. The adaptive-loop contract specifies a draft-rule skill and `codex exec` with constrained commands and restricted source access. This describes the configured development method and intended product use, not proof that a live draft-rule job has run. [Workflow and sparse-worktree restrictions](../AGENTS.md), [draft-rule contract](contracts.md#14-draft-rule-agent-allowed-commands-single-source).

The first commit is `65df901`, with author time **`2026-10-31T09:59:23+07:00`**, exactly as recorded by Git. At snapshot `b639793`, `git rev-list --count HEAD` reports **86 commits in the complete reachable history**. The merged-PR query returned **28 merged records**, with a query limit of 300. These are build-provenance counts, not evaluation results. Sources: `git log --reverse --format="%h %aI %an %s"`, `git rev-list --count HEAD`, and `gh pr list --state merged --limit 300 --json number,title,mergedAt`.

TODO (rule keeper): confirm actual Codex use, including Codex-written tests and parallel worktrees, from approved evidence before making an authorship claim; human Git author metadata alone does not establish assistant use.

No cluster run JSON was available in `eval/clusters/` at this snapshot.

TODO (rule keeper): report draft-rule jobs, attempts, passes and failures from persisted cluster run records, including unsuccessful attempts; no adaptive-loop funnel is claimed in this draft.

## Results and limitations

No eligible oracle report was available in `reports/` at this snapshot. A result may be added only from an `oracle_*.json` report with `standin: false` and an event-date `measured_at`. Every result must retain its denominator and measurement timestamp; who-decided shares must use the report's own `who_decided_v1` or `who_decided_v2` denominator. Test fixtures, unit-test totals, demo outcomes and commitment metadata are not substitutes. [Oracle report contract](contracts.md#5-records).

TODO (rule keeper): sealed-set numbers (first pass, then v1 → v2 replay)

Agreement with a dossier does not prove that the dossier is true. The Kera source register documents the declared vegetable ingredients, later contradictory findings and withdrawal of the self-declaration. A “ten vegetables” ingredients statement can agree with an inaccurate declaration; external findings and a later recall expose a limitation that textual conformance alone cannot resolve. This is not a claim that a particular demo verdict passed. [Kera sources](../data/demo/kera/SOURCES.md).

Constructed sentences must remain labelled as constructed, press-unattributed speech must remain unattributed, and vote disagreement must go to human review. These attribution and decision requirements are explicit in [the contracts](contracts.md#4-enumerations).

TODO (rule keeper): state the small-sample limitation using the denominator of an eligible oracle report; do not infer the measured population from commitment metadata.

TODO (rule keeper): confirm the complete demonstration on the presenting laptop, with its actual configuration and unresolved scenes, before declaring that the demo passed.
