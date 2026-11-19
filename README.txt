LuminaReader
============

Chat-first reading workspace with source references, bounded tool execution,
document-isolated answer caches, reusable workflows and persistent usage totals.

This repository contains application code and offline tests. Book texts,
databases, conversation caches, usage ledgers, API credentials and generated
build output are intentionally excluded. A Git checkout does not restore the
private contents of data/ or browser-local discussion history.

Run the current version
-----------------------
Verified runtime: Python 3.12; Node.js with npm.

1. python3 -m venv .venv
2. .venv/bin/pip install -r requirements.txt
3. npm ci
4. Copy .env.example to .env and configure the model and database location.
   Export OPENAI_API_KEY in the shell running the API, or set
   LUMINA_OPENAI_ENV_FILE to an existing private env file. Never commit a key.
5. .venv/bin/python src/api/app.py
6. In a second terminal: npm run dev -- --host 127.0.0.1
7. Open http://localhost:5173 and load a TXT or Markdown document.

An existing data/reader_novel.db can be reused. Fresh TXT/Markdown imports are
split and indexed locally; import does not automatically launch paid chapter
analysis. Without existing analyzed data, retrieval uses original text.

Model selection remains separate from the harness. The current API adapter
supports OpenAI Responses and an OpenAI-compatible Qwen endpoint. Self-hosted
token usage is shown without inventing a hardware-cost rate.

Verification (no paid model calls)
----------------------------------
.venv/bin/python -m unittest discover -s tests -q
npm run build
git diff --check

Architecture and source entry points
--------------------------------------
docs/reader-architecture.html                 current implementation reference
src/core/pipelines/novel/HARNESS.txt           protocol and default limits
src/api/app.py                               HTTP entry points
src/core/pipelines/novel/chat_runtime.py       jobs, checkpoints and answer cache
src/core/pipelines/novel/reader_harness.py     plan/act/observe loop
src/core/pipelines/novel/reader_tools.py       bounded reading and aggregation
src/core/pipelines/novel/spending.py           persistent accounting
src/core/pipelines/novel/batch.py              optional paid chapter analysis

Current limits
----------------
The chapter analysis stores a short summary, key-event strings and people.
It does not yet provide a verified cross-chapter event graph, narrative-phase
boundaries, causal links, foreshadowing payoffs or character knowledge timelines.
Valid citation IDs do not by themselves prove a claim is entailed by its source.
The next design must preserve these distinctions and the cost controls.
