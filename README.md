# Grounded Claim Ledger

A platform-side conformance layer for livestream selling of food and health-supplement products in Vietnam. It splits what the host says into atomic claims, compares each claim with the legal dossier of the exact product at the broadcast date (standard: "permitted by the dossier"), and stores the result as timestamped evidence with the law article and a verbatim quote. Code decides first. LLMs only extract claims and cast two votes. Anything without a legal or dossier basis goes to a human.

## Prebuilt data (declared)

Only `data/` was imported, unmodified, from the [prebuilt data repository at commit 7663e92](https://github.com/hungkhuu404/OpenAIxSea-On_the_mic-data/tree/7663e92).

## How to run

Start with `python -m core.config`. See the [Commands table in AGENTS.md](AGENTS.md#commands) for tests and application commands.

## Status

TODO
