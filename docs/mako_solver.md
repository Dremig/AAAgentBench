# Mako Solver Guide

This document explains how to run AAAgentBench with the `mako` solver.

## 1) Prerequisites

Directory layout (recommended):

```text
.../ctf-agent/
  AAAgentBench/
  mako/
```

Required runtime dependencies:

1. Docker must be available and running.
2. `AAAgentBench` dependencies installed (`uv sync`).
3. `mako` repo configured with valid API settings in `.env`.

Required `mako` env variables (example):

```env
OPENAI_API_KEY=<your-key>
OPENAI_BASE_URL=https://yunwu.ai/v1
```

## 2) Verify CLI options

```bash
cd AAAgentBench
uv run python -m src.run --help
```

You should see `--solver {codex,mako,manual,pentagi}` and options starting with `--mako-`.

## 3) Run one NYU testcase with mako

```bash
cd AAAgentBench
uv run python -m src.run \
  --platform nyu \
  --solver mako \
  --testcase 2021q-web-poem_collection \
  --split test \
  --timeout-sec 3600 \
  --max-attempts 2 \
  --mako-root ../mako \
  --mako-env .env \
  --mako-index rag_data/index.jsonl \
  --mako-max-steps 8 \
  --mako-cmd-timeout 25 \
  --mako-worker-mode threaded \
  --save-result
```

## 4) Run all NYU targets (same split)

```bash
cd AAAgentBench
uv run python -m src.run \
  --platform nyu \
  --solver mako \
  --run-all \
  --split test \
  --timeout-sec 3600 \
  --mako-root ../mako
```

## 5) Result files and summaries

When `--save-result` is enabled, each target result is stored at:

```text
results/nyu/mako/<target_id>.json
```

Build a summary without rerunning targets:

```bash
cd AAAgentBench
uv run python -m src.run \
  --platform nyu \
  --solver mako \
  --summary-from-results
```

Force rerun a saved testcase:

```bash
cd AAAgentBench
uv run python -m src.run \
  --platform nyu \
  --solver mako \
  --testcase 2021q-web-poem_collection \
  --save-result \
  --force-rerun
```

## 6) Common issues

1. `mako repo not found or invalid`
Cause: `--mako-root` does not point to a valid mako checkout.
Fix: set `--mako-root` to a directory containing `web_agent/cmd_agent.py`.

2. `ModuleNotFoundError: nyuctf`
Cause: AAAgentBench environment not initialized.
Fix: run `uv sync` in `AAAgentBench`.

3. Model/API failures (401/429/timeout)
Cause: invalid key, provider limits, or unstable network.
Fix: verify `../mako/.env` and retry with lower concurrency / fewer steps.

4. Docker build/container startup failures
Cause: challenge image incompatibility or missing architecture support.
Fix: inspect Docker logs and classify as environment/build issue (not solver logic issue).

## 7) Implementation note

`mako` solver implementation path:

- `src/solver/mako_solver.py`

It invokes:

- `python3 -m web_agent.cmd_agent` in `mako` repo

and extracts candidate flags from `flag` field or `final_report` in the generated output JSON.
