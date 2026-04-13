# LLM Runtime Strategy

## Purpose

This note explains which parts of PFIA are deterministic, which are hybrid, which are LLM-first, and why the runtime falls back automatically when OpenAI is unavailable.

## Runtime Router

PFIA uses a simple rule:

- if `PFIA_GENERATION_BACKEND=openai` and `OPENAI_API_KEY` is configured, the service attempts the OpenAI-backed agent path;
- if the key is missing, the upstream call fails, or the model returns unusable output, PFIA falls back to the deterministic path instead of failing the whole job.

This keeps the PoC demoable and deployable even when external APIs are unavailable.

## Decision Matrix

| Module | Runtime mode | Why |
|---|---|---|
| Input parsing / schema validation | deterministic | strict contract, no ambiguity |
| Batch / upload limits | deterministic | infrastructure guardrail |
| PII masking and privacy gate | deterministic | privacy-critical and auditable |
| Deduplication | deterministic | exact-match hashing is cheaper and more reliable |
| Basic language detection | deterministic | cheap and sufficient for coarse routing |
| Spam / injection / low-information review | hybrid | heuristics first, LLM second-pass only for borderline cases |
| Embeddings / retrieval | hybrid | semantic quality with lexical fallback |
| Clustering | deterministic | reproducibility, cost control, and easier debugging |
| Cluster merge / split review | hybrid | deterministic base clustering + LLM semantic review |
| Cluster labeling / summaries | LLM-first | interpretation and phrasing are the model's strong side |
| Priority scoring | deterministic | must stay explainable and stable |
| Anomaly detection | deterministic | statistical rule is cheaper and easier to audit |
| Anomaly explanation | LLM-first | human-readable explanation benefits from language modeling |
| Executive summary | LLM-first | narrative synthesis over structured evidence |
| Q&A planning | LLM-first | tool selection and sequencing are agentic tasks |
| Q&A answer writing | LLM-first | grounded answer composition is a generation task |
| Tool execution | deterministic | tools should execute, not improvise |
| Job state machine / retries / persistence | deterministic | orchestration must remain reliable |

## Why This Split

PFIA treats LLMs as a semantic interpretation layer, not as the system of record.

The deterministic core remains responsible for:

- correctness of parsing and storage;
- privacy and PII guarantees;
- reproducible clustering / scoring / anomaly math;
- retry, recovery, and readiness behavior.

The LLM layer is applied where it creates clear value:

- understanding whether a borderline heuristic match is a real issue;
- improving human-readable cluster labels and summaries;
- reviewing cluster quality beyond lexical similarity;
- rewriting anomaly reasons for a product manager;
- planning and composing grounded answers in Q&A.

## Agents Implemented in Code

Current OpenAI-backed agents:

- `PreprocessReviewAgent`
- `ClusterReviewAgent`
- `TaxonomyAgent`
- `AnomalyExplainerAgent`
- `ExecutiveSummaryAgent`
- `QueryPlannerAgent`
- `AnswerWriterAgent`

These agents only receive sanitized or aggregated data. Raw PII is not intentionally sent into the LLM path.

## Operational Principle

The design goal is:

- better product-facing outputs when OpenAI is available;
- stable PoC behavior when OpenAI is unavailable.

That is why PFIA is built as `LLM-enhanced`, not `LLM-dependent`.
