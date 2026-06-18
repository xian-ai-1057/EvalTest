# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-made benchmarking harness to evaluate whether an **RTX PRO 6000 (Blackwell)** can replace **H100** inference/serving workloads. It fires requests at an inference endpoint, measures latency and throughput, and emits **per-request CSV detail + a console mean/percentile summary** to judge whether SLAs are met and locate bottlenecks. The reference scenarios come from `H100 vs PRO6000 通用測試情境表.xlsx`.

Code comments, docstrings, and user-facing console strings are written in **Traditional Chinese (繁體中文)**. Match that convention when editing.

## Hard design constraints (do not violate)

- **Stdlib-only except `requests`.** This is the core NFR, not a preference. There is no `python-dotenv` (config.py hand-rolls a `.env` parser), no `numpy` (metrics.py hand-rolls percentiles), no `pytest` (tests are plain assertion scripts). Do **not** add dependencies to solve a problem the standard library already covers.
- **Target Python 3.12** (kept 3.11-compatible for local verification).
- Not a production project — it optimizes for "get the numbers, find the problem", not exhaustive fault tolerance.

## Commands

There is no build step and no linter. Everything runs as a plain script.

```bash
pip install -r requirements.txt        # only `requests`
cp .env.example .env                   # then edit endpoint/label
python config.py                       # sanity-check that .env loads (prints typed Config; masks API_KEY)
```

Verification without a GPU (spins up an in-process fake OpenAI SSE server and exercises the whole chain):

```bash
python tests/test_integration.py       # the full test suite — adapter→runner→metrics→reporter
python tests/mock_server.py --port 8000 # run the fake OpenAI/`/predict` server standalone
```

`test_integration.py` is a single assertion-based script (no test framework, no per-test selection). To run "one test", run the script and read its checklist output. When adding behavior, extend this file with another `_check(...)` block.

Scenario scripts are invoked directly (see the README table for the full list and flags):

```bash
python scenarios/s1_interactive.py --n 20 --max-tokens 256 --label H100-FP8   # ① single-request latency (concurrency=1)
python scenarios/s2_concurrency.py --concurrency 1,8,16,32 --n 50             # ② concurrency sweep vs SLA
python scenarios/s_vlm.py --images "data/images/*" --concurrency 1            # VLM image→text
```

To compare cards: point `BASE_URL`/`MODEL` at each endpoint, set a distinct `RUN_LABEL` (e.g. `H100-FP8` vs `PRO6000-FP4`), run each scenario once per card, then diff the resulting CSVs in `results/` (gitignored).

## Architecture

The whole system pivots on **two contracts**; understand these before changing anything.

1. **`RequestResult` (core/metrics.py)** — the single dataclass that flows through the entire pipeline (adapter → runner → metrics → reporter). **CSV column order == field declaration order** of this dataclass (reporter.py writes via `field_names()`), so adding/reordering fields changes the output schema.

2. **The adapter `call()` contract (core/client.py)** — every adapter implements exactly:
   ```python
   call(payload, *, max_tokens, temperature, stream) -> RequestResult
   ```
   The runner depends only on this; it knows nothing about HTTP. **`make_adapter(config)` is the registry**: it maps the `ADAPTER` env value to a class. Supporting a new backend = write a class with `call()`, register a name branch in `make_adapter()`, and (if needed) add config fields — **runner, metrics, reporter, and all scenario scripts stay untouched.** This is the single intended seam (`FR9`).

Built-in adapters and how to reach each (no code change for the first three):
- `openai_chat` → `OpenAIChatAdapter`: OpenAI-compatible `/v1/chat/completions`. SSE streaming measures TTFT/TPOT.
- `generic_json` → `GenericJSONAdapter`: configurable single-shot JSON service (BERT/STT/custom). `{input}`/`{max_tokens}` template + dotted `response_path`. e2e only.
- `vlm` → `VLMAdapter`: image→text HTTP JSON with base64 (`{image_b64}`/`{prompt}`). e2e only.
- `package` → `CallableAdapter`: in-process Python package, no HTTP (edit `core/package_adapter_example.py`). A generator return measures TTFT/TPOT; a string return measures e2e.
- `custom` → copy `core/custom_adapter_example.py`, implement 3 TODOs (`core/client.py` lazy-imports it).

**Measurement rule:** TTFT/TPOT require streaming. Only `OpenAIChatAdapter` (SSE) and `CallableAdapter` (generator) produce them; everything else degrades gracefully to end-to-end latency. Single-request `tokens_per_s` is computed over the decode phase (`e2e − ttft`), not wall time; system throughput in `summarize()` is `Σtokens ÷ wall_seconds` from the concurrent runner.

**Scenario scripts (scenarios/) are deliberately thin and uniform.** Each follows the same pipeline: `Config.load()` → `make_adapter()` → optional `GpuSampler.start()` → `run_single`/`run_concurrent` → `GpuSampler.stop()` → `summarize()` → `write_csv()` + `print_summary()`. The only per-scenario differences are single vs concurrent, `stream` True/False, and a scenario-specific derived metric printed at the end (e.g. QPS in s4, RTF in s6, items/hour in s3, SLA max-concurrency in s2). Keep new scenarios in this mold rather than adding logic to the core.

**Config (config.py):** real `os.environ` takes precedence over the `.env` file (so CI/ad-hoc overrides work). `Config` is a typed dataclass loaded via `Config.load()`.

**Import convention:** scenario and test scripts prepend the repo root to `sys.path` so `python scenarios/foo.py` resolves `config`/`core` imports. Run scripts by path (as above); do not convert these to package-relative imports.

**GPU monitoring (core/gpu.py):** `GpuSampler` background-polls local `nvidia-smi`. With no `nvidia-smi` present, `start()` is a no-op and `stop()` returns `None` — scenarios run normally on a GPU-less client/load box.

## Specs

`specs/001-h100-pro6000/` holds the source-of-truth `spec.md` (FR/AC requirements + traceability), `plan.md` (the interface contracts above), and `tasks.md`. When changing behavior, keep it consistent with the FR/AC numbering referenced throughout the code and tests.
