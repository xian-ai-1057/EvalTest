# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A minimal self-made benchmarking tool to evaluate whether an **RTX PRO 6000 (Blackwell)** can replace **H100** inference/serving workloads. It fires requests at an OpenAI-compatible (or custom) chat endpoint, measures latency and throughput, and emits **one Excel report per run (sheet 1 = aggregated stats summary, sheet 2 = per-request detail, sheet 3 = this run's parameters), a sibling JSON, and a console mean/percentile summary**. The JSON is a single structured dict — `{summary, params, detail}` — where `detail` is the full per-request list (and the only part `accuracy.py` reads). The `.xlsx` is written via **pandas + openpyxl** (sheet 2 = `DataFrame.to_excel`; sheets 1/3 reuse a small ragged row-builder). Sheet 2 / the JSON `detail` carry the original texts (`input_text`, `reasoning_text` 思考內容, `output_text`, `answer` 正解), and the JSON `detail` additionally keeps the full `raw_response`.

The project is **a thin benchmark entry (`simple_bench.py`) over feature modules in `core/`, plus a standalone `accuracy.py`**:

- **`simple_bench.py`** — the benchmark entry. Holds only the top "參數設定區" (plain Python variables; no CLI args, no `.env`) and a short linear `main()` pipeline that wires the `core/` modules. Run: `python simple_bench.py`.
- **`accuracy.py`** — standalone (single-file, uses `core/xlsx.py`). Reads `simple_bench`'s JSON (its `detail`) or a CSV, compares a "正解" field vs a "回覆" field by exact match, prints accuracy + a 2-sheet Excel. Run: `python accuracy.py`.

Code comments, docstrings, and user-facing console strings are written in **Traditional Chinese (繁體中文)**. Match that convention when editing.

## Hard design constraints (do not violate)

- **Dependencies: `requests` + `pandas` + `openpyxl` only.** `pandas`/`openpyxl` are used **only for output** (the report DataFrame → Excel). Everything else stays standard-library: `core/metrics.py` still hand-rolls percentiles (no `numpy`), the test is still a plain assertion script (no `pytest`), and `core/xlsx.py` (stdlib zip+XML) is kept for `accuracy.py`. Do **not** pull pandas/numpy into the metrics or request path, and do **not** add further dependencies for things the standard library already covers.
- **Target Python 3.12** (kept 3.11-compatible for local verification).
- Not a production project — it optimizes for "get the numbers, find the problem", not exhaustive fault tolerance.

## Commands

There is no build step and no linter. Everything runs as a plain script.

```bash
pip install -r requirements.txt        # requests + pandas + openpyxl
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

**`RequestResult` (core/metrics.py)** — the single dataclass that flows through the pipeline (`core/client.py` fills it → `core/runner.py` stamps it → `summarize()` aggregates → `reporter` writes it out). **Excel detail-sheet (sheet 2) column order == field declaration order** of this dataclass (`reporter.py` builds the detail `DataFrame` with `columns=field_names()`), so adding/reordering fields changes the output schema. One exception: fields tagged `metadata={"csv": False}` (currently only `raw_response`, the full original response) are skipped by `field_names()` and live **only in the JSON `detail`**. `client`/`runner` set `input_text`/`reasoning_text`/`output_text`/`answer`/`raw_response` for human inspection; only `answer` feeds a downstream step (`accuracy.py`).

**Pipeline** — `simple_bench.main()` builds a `BenchConfig` (core/config.py) from the 參數設定區, then: `dataset.build_pairs()` (synthetic via `build_prompt()`, or `load_dataset()` for CSV/TXT → `[(prompt, answer), …]`) → `runner.run_benchmark(pairs, cfg)` → `summarize()` → `print_summary()` + `reporter.write_outputs(..., params=cfg.as_params(...))`. **`runner` is where the body is built** (`cfg.body_builder or payload.build_body`) and where each result is stamped (`index`/`concurrency`/`scenario`/`run_label`/`answer`/`input_text`); it submits `client.call(url, headers, body, *, stream, response_parser, timeout)` to a ThreadPoolExecutor. **`client.call` only calls** — it dispatches to `_decode_stream()` (SSE → TTFT/TPOT) or `_decode_once()` (e2e only, optional `response_parser` for non-OpenAI responses) and does **not** build the body. `payload.build_body()` sets `chat_template_kwargs.enable_thinking` (思考模式) and, when streaming, `stream_options.include_usage`.

**Output helpers (core/reporter.py)** — `build_report(results, summary, params=None) -> dict` is the canonical "output result": `{summary: asdict(Summary), params: {…}, detail: [per-request dicts]}` (the `detail` is built from `asdict`, **not** a DataFrame, so `None` stays `null` and ints stay ints). `write_outputs(results, summary, base_path, *, params=None) -> (xlsx, json)` writes that dict to JSON and a 3-sheet `.xlsx` via `pandas.ExcelWriter(engine="openpyxl")` — sheet 2 (detail) is a `DataFrame.to_excel`; sheets 1/3 reuse the ragged row-builders (`_summary_sheet_rows`/`_params_rows`, padded to equal width). `print_summary(summary)` prints the console table. `core/xlsx.py` (stdlib zip+XML `write_workbook`) is **now used only by `accuracy.py`**.

**Measurement rule:** TTFT/TPOT require streaming (`STREAM=True`); non-streaming degrades to end-to-end latency. Single-request `tokens_per_s` is computed over the decode phase (`e2e − ttft`), not wall time; system throughput in `summarize()` is `Σtokens ÷ wall_seconds` from the concurrent runner. **Reasoning models:** `reasoning_content` (相容 `reasoning`) and `content` are treated alike — both count toward TTFT (first token of *either*), TPOT, `output_tokens` and `output_chars` — so throughput stays consistent with `usage.completion_tokens` (which includes reasoning) and isn't inflated. `output_text` keeps only the final answer; `output_chars` is the reasoning+content total.

**Config convention:** Both tools are still configured by editing the 參數設定區 block at the top of the file (plain Python variables); there is no `.env`. `simple_bench`'s 參數設定區 vars are collected into a `BenchConfig` dataclass (core/config.py) inside `main()` and passed down the pipeline — config *transport*, not a config *file* (you still edit the variables, and `BenchConfig.as_params()` is the single source for the 執行參數 sheet).
