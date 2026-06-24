# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A minimal self-made benchmarking tool to evaluate whether an **RTX PRO 6000 (Blackwell)** can replace **H100** inference/serving workloads. It fires requests at an OpenAI-compatible `/v1/chat/completions` endpoint, measures latency and throughput, and emits **one Excel report per run (sheet 1 = aggregated stats summary, sheet 2 = per-request detail, sheet 3 = this run's parameters), a sibling JSON of the full per-request detail, and a console mean/percentile summary**. The `.xlsx` is hand-written with stdlib only (`core/xlsx.py` — zip + XML, no openpyxl). Sheet 2 / the JSON carry the original texts (`input_text`, `reasoning_text` 思考內容, `output_text`, `answer` 正解), and the JSON additionally keeps the full `raw_response`.

The project is just **two single-file tools** plus the shared `core/` output helpers:

- **`simple_bench.py`** — the benchmark. Inlines `requests` calls + timing + concurrency (no adapter/runner abstraction). All config is in the top "參數設定區" (plain Python variables; no CLI args, no `.env`). Run: `python simple_bench.py`.
- **`accuracy.py`** — reads `simple_bench`'s JSON (or a CSV), compares a "正解" field vs a "回覆" field by exact match, prints accuracy + a 2-sheet Excel. Run: `python accuracy.py`.

Code comments, docstrings, and user-facing console strings are written in **Traditional Chinese (繁體中文)**. Match that convention when editing.

## Hard design constraints (do not violate)

- **Stdlib-only except `requests`.** This is the core NFR, not a preference. No `numpy` (metrics.py hand-rolls percentiles), no `openpyxl` (xlsx.py hand-writes the workbook), no `pytest` (the test is a plain assertion script). Do **not** add dependencies to solve a problem the standard library already covers.
- **Target Python 3.12** (kept 3.11-compatible for local verification).
- Not a production project — it optimizes for "get the numbers, find the problem", not exhaustive fault tolerance.

## Commands

There is no build step and no linter. Everything runs as a plain script.

```bash
pip install -r requirements.txt        # only `requests`
python simple_bench.py                 # edit the 參數設定區 at the top first
python accuracy.py                     # edit INPUT_PATH/ANSWER_FIELD/REPLY_FIELD first
```

Verification without a GPU (spins up an in-process fake OpenAI SSE server and exercises both tools):

```bash
python tests/test_integration.py        # smoke test: simple_bench + accuracy against the mock server
python tests/mock_server.py --port 8000 # run the fake OpenAI server standalone
```

`test_integration.py` is a single assertion-based script (no test framework, no per-test selection). To run "one test", run the script and read its checklist output. When adding behavior, extend this file with another `_check(...)` block.

## Architecture

The whole system pivots on **one contract**; understand it before changing anything.

**`RequestResult` (core/metrics.py)** — the single dataclass that flows through the pipeline (`simple_bench` builds it → `summarize()` aggregates → `reporter` writes it out). **Excel detail-sheet (sheet 2) column order == field declaration order** of this dataclass (`reporter.py` builds the sheet via `field_names()`), so adding/reordering fields changes the output schema. One exception: fields tagged `metadata={"csv": False}` (currently only `raw_response`, the full original response) are skipped by `field_names()` and live **only in the JSON detail**. `simple_bench` sets `input_text`/`reasoning_text`/`output_text`/`answer`/`raw_response` for human inspection; only `answer` feeds a downstream step (`accuracy.py`).

**`simple_bench.py` flow** — `build_pairs()` (synthetic input via `build_prompt()`, or `load_dataset()` for CSV/TXT → `[(prompt, answer), …]`) → `run_concurrent_simple()` (ThreadPoolExecutor; calls `chat_once()` per request, stamps `index`/`concurrency`/`scenario`/`run_label`/`answer`) → `summarize()` → `print_summary()` + `write_outputs(..., params=…)`. `chat_once()` dispatches to `_decode_stream()` (SSE → TTFT/TPOT) or `_decode_once()` (e2e only); `_build_body()` sets `chat_template_kwargs.enable_thinking` (思考模式) and, when streaming, `stream_options.include_usage`.

**Output helpers (core/reporter.py)** — `write_outputs(results, summaries, base_path, *, gpu_stats_list=None, params=None)` emits one `.xlsx` (sheet 1 = summary from `summaries`, sheet 2 = per-request detail, optional sheet 3 = `params` 執行參數) + the sibling JSON together; it does not re-summarize. `print_summary(summary, gpu_stats=None)` prints the console table. `core/xlsx.py` is the stdlib `.xlsx` writer (`write_workbook(path, [(sheet_name, rows), …])`) — used by both `reporter.py` and `accuracy.py`.

**Measurement rule:** TTFT/TPOT require streaming (`STREAM=True`); non-streaming degrades to end-to-end latency. Single-request `tokens_per_s` is computed over the decode phase (`e2e − ttft`), not wall time; system throughput in `summarize()` is `Σtokens ÷ wall_seconds` from the concurrent runner. **Reasoning models:** `reasoning_content` (相容 `reasoning`) and `content` are treated alike — both count toward TTFT (first token of *either*), TPOT, `output_tokens` and `output_chars` — so throughput stays consistent with `usage.completion_tokens` (which includes reasoning) and isn't inflated. `output_text` keeps only the final answer; `output_chars` is the reasoning+content total.

**Config convention:** Both tools are configured by editing the 參數設定區 block at the top of the file (plain Python variables). There is no `.env` and no config module.
