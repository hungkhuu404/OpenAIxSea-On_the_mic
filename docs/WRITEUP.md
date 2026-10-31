# Grounded Claim Ledger

Grounded Claim Ledger helps a platform compare claims made during Vietnamese livestream sales with the legal dossier of the exact product at the broadcast date. Its standard is **permitted by the dossier at the broadcast date**. Each decision connects the spoken claim, a dossier quotation and the applicable law, so a reviewer can see why the system acted. [Project overview](../README.md)

## The problem

A seller can move from describing ingredients to promising a treatment in the same turn. The platform needs to separate those claims, identify the product, and compare each claim with the dossier that applied when it was broadcast. A registration, advertising confirmation or recall can change the answer over time. A plausible sentence alone is insufficient grounds for a decision.

TODO (rule keeper): confirm and cite the statutory requirement for platforms to set their own livestream-selling control measures, and the absence of a prescribed method for comparing speech with a product dossier at the broadcast date.

## From speech to a reviewable decision

The pipeline is **S0 context → S1 claim extraction → S2 router A–F → S3 aggregation → append-only ledger**. S0 fixes the product and date, builds the permitted-item set from its dossier, and excludes low-confidence material. S1 splits a turn into atomic claims and locates them in the original text. Each atom keeps its speaker attribution and claim axis, such as ingredients or declared function. [Pipeline contracts](contracts.md)

The router lets code decide first. Step A recognizes permitted wording; B applies legal rules; C is reserved for low-confidence speech recognition; D checks whether the product, subject and dossier provide enough basis to continue. Only unresolved claims reach E, where two LLM votes compare the claim with the relevant slice of permitted dossier items. One vote assesses support, while the other identifies an entailing dossier item. Agreement can produce green, “Within file,” or yellow, “Beyond file.” Disagreement and insufficient information become abstain, “Needs human.” **Red, “Prohibited,” comes only from code rules or session flags.**

Code checks every quotation and highlighted span against its source text. Step F resolves dossier quotations by permitted-item identifier and filters law anchors by their effective date and the rule's applicability window at the broadcast date. Without an applicable legal basis, the claim goes to a human. The LLM cannot invent a source quotation or turn its own conclusion into red. [Decision invariants](../AGENTS.md#hard-invariants-tests-enforce-them-never-break-them)

S3 combines atom verdicts into turn and session outcomes. The ledger appends verdicts and events, preserving earlier decisions when a later pack supersedes them. The interface puts the claim, dossier quotation and legal basis together and shows who decided: code, LLM votes or human review. Its public mode uses replay and code-only sentence checks without calling an LLM or Codex. [Records and interface](contracts.md)

## Autonomous & Adaptive, with asymmetric authority

Unresolved claims enter a review queue. A human label adds correction memory; clustering groups related items into a draft-rule job. Codex receives that cluster in a sparse worktree containing the rule-writing inputs and law corpus, without the evaluation sets. It proposes a rule, metadata and tests. An external gate checks the candidate and its access log; failed attempts return error lines for a bounded retry. Sealed-set measurement and a human merge stand between the proposal and live decisions. [Adaptive-loop contracts](contracts.md#8-module-interfaces-names-are-fixed-signatures-are-prose)

The control policy is asymmetric autonomy: alone, the system can only become more careful. Under this policy, recalls apply automatically; a quarantined rule moves affected decisions toward human review. A new rule or a dossier change that makes outcomes greener needs approval. Similarity helps rank and cluster review items and never chooses a colour. A dry-run preview and backfill are part of the human merge design.

TODO (rule keeper): confirm the complete live adaptive loop, including recall handling and human merge, before claiming a successful end-to-end run.

TODO (rule keeper): report draft-rule job totals, attempts, passed and failed outcomes from eval/clusters/*.json, including failed attempts and each total's population; no cluster run file is present in this snapshot.

## Deep Domain: law and dossiers through time

The system distinguishes health-protection foods from food supplements because their advertising bases differ. Law anchors carry effective dates; dossiers carry declaration, advertising-confirmation and recall states. The same speech can therefore receive different treatment at different broadcast dates without erasing its history. Official records, press quotations and lower-confidence material retain their provenance, and only eligible dossier fields enter the permitted-item set. [Law sources](../data/law/SOURCES.md), [registry sources](../data/registry/SOURCES.md), [permitted-item contract](contracts.md#6-permitted-items-axis-slices-and-step-d)

## How Codex fits the build and the product

`AGENTS.md` gives Codex the project rules and workspace boundaries; `docs/contracts.md` supplies shared names, records and interfaces. These context files let changes across the pipeline, ledger and UI use the same contracts. The development workflow separates changes into branches with tests and review. In the product itself, `codex exec` runs the draft-rule skill inside the restricted adaptive worktree; the gate runs outside that worktree. [Development workflow](../AGENTS.md#git-workflow-every-task), [draft-rule command contract](contracts.md#14-draft-rule-agent-allowed-commands-single-source)

TODO (rule keeper): confirm actual development use of parallel worktrees and Codex-written tests before submission.

At main snapshot `933463e`, the history has 120 reachable commits. The merged-PR query returns 41 records with a retrieval limit of 300. These describe development history, rather than product accuracy. Sources: `git log --reverse --format="%h %aI %an %s"`, `git rev-list --count HEAD`, and `gh pr list --state merged --limit 300 --json number,title,mergedAt`.

## Results and limits

TODO (rule keeper): sealed-set numbers (first pass, then v1 → v2 replay)

TODO (rule keeper): add results only from reports/oracle_*.json with standin=false and measured_at on 2026-10-31; include n and measured_at for every result, and use who_decided_v1 / who_decided_v2 with their own denominators for decision shares. No eligible report is present in this snapshot.

Conformance cannot establish that a dossier is true. In the Kera case, a claim about ten vegetables can match a false declaration. The source record documents later contradictory findings and withdrawal of the declaration; the recall gives the system grounds to act on that problem. Comparing speech with the earlier dossier alone cannot discover it. [Kera source record](../data/demo/kera/SOURCES.md)

The sealed set is small, so a change on a few cases can alter an aggregate substantially. Constructed sentences stay labelled as constructed; they are not presented as original livestream quotations. Speech the press does not attribute remains “Speaker unknown.” When the two votes disagree, the claim goes to a human.

TODO (rule keeper): state the sealed-set sample size and limits using n from an eligible oracle report.

TODO (rule keeper): confirm the actual demonstration on the presenting laptop before describing a successful demo.
