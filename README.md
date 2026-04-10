# AAAgentBench

AAAgentBench is a small benchmark harness for evaluating agent solvers on CTF-style tasks.

## Environment

This project uses `uv`.

Setup:

```bash
cd AAAgentBench
uv sync
```

Run the CLI:

```bash
uv run python -m src.run --help
```

## Available Platforms

- `nyu`: NYU CTF Bench via the `nyuctf` package

## Available Solvers

- `codex`: runs `codex exec` non-interactively and validates flags automatically
- `manual`: simple interactive solver for testing
- `pentagi`: uses PentAGI API to create/poll flows and auto-submit extracted flag candidates
- `mako`: runs local `mako` repo `web_agent.cmd_agent` and submits extracted flag candidates

## Mako Quick Start

1. Ensure local layout:

```text
.../ctf-agent/
  AAAgentBench/
  mako/
```

2. Ensure `../mako/.env` contains a valid API config (for example `OPENAI_API_KEY` and `OPENAI_BASE_URL=https://yunwu.ai/v1`).
3. Run one testcase:

```bash
uv run python -m src.run \
  --platform nyu \
  --solver mako \
  --testcase 2021q-web-poem_collection \
  --split test \
  --timeout-sec 3600 \
  --max-attempts 2 \
  --mako-root ../mako \
  --mako-max-steps 8 \
  --save-result
```

Detailed guide: [docs/mako_solver.md](docs/mako_solver.md)

## Usage

Run a single testcase:

```bash
uv run python -m src.run \
  --platform nyu \
  --solver codex \
  --testcase 2017q-cry-almost_xor \
  --split test \
  --timeout-sec 3600
```

Run all testcases in a split:

```bash
uv run python -m src.run \
  --platform nyu \
  --solver codex \
  --run-all \
  --split test
```

Useful options:

- `--platform`: select platform implementation
- `--solver`: select solver implementation
- `--testcase`: run one target id
- `--run-all`: run the whole selected split
- `--save-result`: save one testcase result locally
- `--results-dir`: choose where local result files are stored
- `--summary-from-results`: summarize saved testcase results without rerunning
- `--force-rerun`: ignore cached saved results and rerun targets
- `--split`: dataset split, usually `test` or `development`
- `--timeout-sec`: per-target timeout
- `--log-level`: logging verbosity
- `--model`: model name for `codex`
- `--max-attempts`: retry count for solvers that support it
- `--sandbox-mode`: Codex sandbox mode
- `--pentagi-api`: PentAGI API base URL (for `--solver pentagi`)
- `--pentagi-user`: PentAGI login email
- `--pentagi-pass`: PentAGI login password
- `--pentagi-provider`: provider name used for flow creation, e.g. `custom`
- `--pentagi-poll-interval-sec`: flow polling interval
- `--pentagi-max-wait-sec`: max wait time per target
- `--pentagi-verify-tls`: enable TLS verification (default is insecure for local self-signed certs)
- `--mako-root`: local path to `mako` repository root (for `--solver mako`)
- `--mako-env`: env file path relative to mako root
- `--mako-index`: index path relative to mako root
- `--mako-max-steps`: max challenge steps per mako attempt
- `--mako-cmd-timeout`: command timeout passed to mako cmd_agent
- `--mako-worker-mode`: `threaded` or `sync`

Run with PentAGI solver:

```bash
uv run python -m src.run \
  --platform nyu \
  --solver pentagi \
  --testcase 2017q-web-historypeats \
  --split test \
  --timeout-sec 3600 \
  --pentagi-api https://127.0.0.1:8443 \
  --pentagi-user admin@pentagi.com \
  --pentagi-pass admin \
  --pentagi-provider custom
```

Run with Mako solver:

```bash
uv run python -m src.run \
  --platform nyu \
  --solver mako \
  --testcase 2021q-web-poem_collection \
  --split test \
  --timeout-sec 3600 \
  --max-attempts 2 \
  --mako-root ../mako \
  --mako-max-steps 8
```

## Adding a Solver

1. Add a new solver class under [solver](src/solver).
2. Implement the interface in [solver/base.py](src/solver/base.py).
3. Register the solver name in [solver/__init__.py](src/solver/__init__.py).
4. Expose any solver-specific CLI options in [run.py](src/run.py) if needed.

Minimal interface:

```python
class BaseSolver(ABC):
    @abstractmethod
    def solve(
        self,
        session: Session,
        submit_flag: Callable[[str], ValidationResult],
    ) -> SolveResult:
        ...
```

Return:

- `SolveResult(status="solved", flag=...)` when a valid flag is found
- `SolveResult(status="give_up", flag=None)` when the solver cannot finish

## Saving Single-Run Results

Save one testcase result:

```bash
uv run python -m src.run \
  --platform nyu \
  --solver codex \
  --testcase 2017q-cry-almost_xor \
  --save-result
```

This writes a JSON file to:

```text
results/<platform>/<solver>/<target_id>.json
```

Summarize saved single-target results later:

```bash
uv run python -m src.run \
  --platform nyu \
  --solver codex \
  --summary-from-results
```

This is useful when you run a few cases manually and want to compute summary statistics later without rerunning them.

If you use `--save-result`, both single-target runs and `--run-all` will reuse existing saved results instead of rerunning the same testcase.

To ignore cache and rerun:

```bash
uv run python -m src.run \
  --platform nyu \
  --solver codex \
  --testcase 2017q-cry-almost_xor \
  --save-result \
  --force-rerun
```
