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

Compact-first V2 design (proposal, not deployed)
-----------------------------------------------
docs/compact-reader-v2.html               offline architecture proposal
docs/compact-reader-v2-contracts.json     proposed evidence/data contracts
docs/compact-reader-v2-policy.json        skills, reading-depth and cost policy
docs/compact-reader-v2-evaluation.json    48 UNEXECUTED coverage cases
docs/compact-reader-v2-validation.txt     actual document verification scope

Rebuild the proposal without model calls:
.venv/bin/python scripts/build_reader_design_specs.py
.venv/bin/python scripts/build_compact_reader_design.py

The runnable baseline is preserved as v1.0.0-reader-baseline. The design does
not initiate paid preprocessing, migrate databases or replace the current chat.

Bounded V2 pilot (separate runtime)
-----------------------------------
The optional V2 runtime indexes original-order chapters 1738-1841 only. It adds
source-linked events, character states, candidate clues, cross-chapter links,
phases, and a model-reviewed fate/rank ledger. These records are not a human
adjudication of all facts and do not constitute a full-book V2 rollout.

The pilot uses an independent database, index, answer cache and spending ledger:
data/version-comparison/v2/. Original V1 data and historical answers are reused
read-only. Preprocessing has a persistent USD10 maximum, shared by physical
retries and resumed work. Unknown request usage retains its reservation.
Chapter complexity changes extraction output allowances; required fields remain.

Prepare the isolated source database without a model call:
.venv/bin/python scripts/run_compact_pilot.py --prepare-only

Run/resume preprocessing under the authorized limit (paid model calls):
.venv/bin/python scripts/run_compact_pilot.py --budget 10

Finish cross-phase storylines and distant clues under that SAME limit:
.venv/bin/python scripts/finalize_compact_pilot.py

Run V2 API on another port while V1 remains on 5001:
LUMINA_READER_VERSION=v2 LUMINA_API_PORT=5002 LUMINA_DB_PATH="$PWD/data/version-comparison/v2/book.db" .venv/bin/python src/api/app.py

Run V2 frontend while V1 remains on 5173:
VITE_API_BASE=http://127.0.0.1:5002/api npm run dev -- --host 127.0.0.1 --port 5174 --strictPort

Open http://127.0.0.1:5174/comparison.html for the local comparison workspace.
The comparison runner waits for index publication and invokes only V2:
.venv/bin/python scripts/compare_reader_versions.py

Each V2 question retains the ordinary bounded harness limits, including its
separate default USD0.50 maximum. The preprocessing authorization does not reset
when a worker resumes. Generated request results and accounting stay in data/.

Comparison provenance matters: four saved cases are historical fixed-evidence
GPT generations, with retrieval already supplied. A fifth narrative recap is a
real prior chat, recovered by reproducing its original cache fingerprint. That
chat used 110 source chapters, while this pilot indexes 104; the page labels the
coverage difference. Historical generation cost, new question cost and one-time
preprocessing cost are separate. Missing latency/usage remains unknown. Scores
must name their source-checked criteria; citation existence is not factual proof.

Measured pilot, 2026-11-19
--------------------------
All 104 chapters were indexed, with 515 events, 82 overlapping phase units,
11 story arcs, 399 event links, source-linked clue candidates and a 40-record
fate/rank ledger. Source reference and graph ID checks pass. These checks do
not prove that every generated interpretation is correct.

Preprocessing returned 2,226,721 known tokens at USD8.834349. Two requests lack
complete usage receipts; their USD0.422265 reservations remain held. Known
cost plus unresolved reservations is USD9.256614, within the original USD10.
One official input-token count allowed a tighter final reservation without
increasing that limit. Retries and resumed work are included.

The five historical V1 generations used 289,044 tokens at USD0.850938; no new
V1 generation was made. V2 used 527,264 tokens at USD1.460031 across 30 physical
model requests. Two jobs resumed saved evidence after a conservative budget
stop; the totals include both attempts. Each stayed below USD0.50. Missing
V1 latency remains unknown, and fixed-evidence V1 timings exclude retrieval.

Agent source review found 21/23 shared key items satisfied by V1 and 18/23 by
V2. This is a targeted check, not independent human grading or book-wide
accuracy. V2 omitted the latter battle/aftermath in the narrative recap and
left some explicitly resolved states unknown. Both versions also overstate
rank precision for some people. V2 found a real conflicting rank description
in a paragraph absent from V1's frozen input; that extra finding is not scored
as an equal-input advantage. These results do not establish a V2 improvement.

The final code derives context metadata locally, retains older original
evidence across the observation window and deduplicates repeated ledgers.
The evidence-window changes passed offline regression checks but were not
paid-retested on these five answers; displayed results remain the actual run.
91 offline tests and the frontend build pass. A real replay of a grounded
partial answer made zero model calls and left the spending ledger unchanged.

The local comparison includes full answers, costs, tokens, latency, original
review excerpts and initial stop/continuation records. Private source text,
answer snapshots and ledgers remain under ignored data/version-comparison/.
To continue only previously budget-stopped V2 jobs after a repair, once:
.venv/bin/python scripts/resume_compact_comparison.py
