from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from .platform import AVAILABLE_PLATFORMS, create_platform
from .result.report import summarize_runs
from .result.store import load_run_record, load_run_records, save_run_record
from .runner.benchmark_runner import BenchmarkRunner
from .solver import AVAILABLE_SOLVERS, create_solver

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run AAAgentBench benchmarks.")
    parser.add_argument(
        "--platform",
        choices=sorted(AVAILABLE_PLATFORMS.keys()),
        default="nyu",
        help="Platform implementation to use.",
    )
    parser.add_argument(
        "--solver",
        choices=sorted(AVAILABLE_SOLVERS.keys()),
        default="codex",
        help="Solver implementation to use.",
    )
    parser.add_argument(
        "--testcase",
        "--target-id",
        dest="target_id",
        help="Single target id to run.",
    )
    parser.add_argument(
        "--run-all",
        action="store_true",
        help="Run all targets in the selected platform split.",
    )
    parser.add_argument(
        "--split",
        default="test",
        help="Dataset split for the platform.",
    )
    parser.add_argument(
        "--version",
        default=None,
        help="Dataset version for platforms that support it.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=1800,
        help="Per-target timeout in seconds.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name for solvers that support it.",
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="Maximum solver attempts for solvers that support retries.",
    )
    parser.add_argument(
        "--sandbox-mode",
        default="workspace-write",
        help="Sandbox mode for Codex solver.",
    )
    parser.add_argument(
        "--pentagi-api",
        default="https://127.0.0.1:8443",
        help="PentAGI API base URL for pentagi solver.",
    )
    parser.add_argument(
        "--pentagi-user",
        default="admin@pentagi.com",
        help="PentAGI login email for pentagi solver.",
    )
    parser.add_argument(
        "--pentagi-pass",
        default="admin",
        help="PentAGI login password for pentagi solver.",
    )
    parser.add_argument(
        "--pentagi-provider",
        default="custom",
        help="Provider name used when creating PentAGI flows.",
    )
    parser.add_argument(
        "--pentagi-poll-interval-sec",
        type=float,
        default=5.0,
        help="Polling interval for PentAGI flow status.",
    )
    parser.add_argument(
        "--pentagi-max-wait-sec",
        type=int,
        default=1800,
        help="Max wait time per target for PentAGI flow completion.",
    )
    parser.add_argument(
        "--pentagi-verify-tls",
        action="store_true",
        help="Enable TLS certificate verification for PentAGI API.",
    )
    parser.add_argument(
        "--mako-root",
        default="../mako",
        help="Path to mako repository root (for --solver mako).",
    )
    parser.add_argument(
        "--mako-env",
        default=".env",
        help="Env file path relative to mako root.",
    )
    parser.add_argument(
        "--mako-index",
        default="rag_data/index.jsonl",
        help="RAG index path relative to mako root.",
    )
    parser.add_argument(
        "--mako-max-steps",
        type=int,
        default=12,
        help="Maximum steps per mako attempt.",
    )
    parser.add_argument(
        "--mako-cmd-timeout",
        type=int,
        default=25,
        help="Per-command timeout passed to mako cmd_agent.",
    )
    parser.add_argument(
        "--mako-worker-mode",
        choices=["threaded", "sync"],
        default="threaded",
        help="Worker mode passed to mako cmd_agent.",
    )
    parser.add_argument(
        "--save-result",
        action="store_true",
        help="Save single-target run results to the local results directory.",
    )
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory used for saved single-target results and offline summaries.",
    )
    parser.add_argument(
        "--summary-from-results",
        action="store_true",
        help="Build a summary from previously saved single-target results.",
    )
    parser.add_argument(
        "--force-rerun",
        action="store_true",
        help="Ignore cached single-target results and rerun them.",
    )
    return parser


def create_platform_from_args(args: argparse.Namespace):
    if args.platform == "nyu":
        return create_platform(
            args.platform,
            split=args.split,
            version=args.version,
        )
    return create_platform(args.platform)


def create_solver_from_args(args: argparse.Namespace):
    if args.solver == "codex":
        return create_solver(
            args.solver,
            model=args.model,
            max_attempts=args.max_attempts,
            sandbox_mode=args.sandbox_mode,
        )
    if args.solver == "pentagi":
        return create_solver(
            args.solver,
            pentagi_api=args.pentagi_api,
            pentagi_user=args.pentagi_user,
            pentagi_pass=args.pentagi_pass,
            pentagi_provider=args.pentagi_provider,
            pentagi_poll_interval_sec=args.pentagi_poll_interval_sec,
            pentagi_max_wait_sec=args.pentagi_max_wait_sec,
            pentagi_insecure_tls=not args.pentagi_verify_tls,
        )
    if args.solver == "mako":
        return create_solver(
            args.solver,
            mako_root=args.mako_root,
            mako_env=args.mako_env,
            mako_index=args.mako_index,
            max_attempts=args.max_attempts,
            mako_max_steps=args.mako_max_steps,
            mako_cmd_timeout=args.mako_cmd_timeout,
            mako_worker_mode=args.mako_worker_mode,
        )
    return create_solver(args.solver)


def get_result_path(args: argparse.Namespace) -> Path:
    return Path(args.results_dir) / args.platform / args.solver / f"{args.target_id}.json"


def get_result_path_for_target(args: argparse.Namespace, target_id: str) -> Path:
    return Path(args.results_dir) / args.platform / args.solver / f"{target_id}.json"


def run_or_load_record(
    *,
    args: argparse.Namespace,
    runner: BenchmarkRunner,
    platform,
    solver,
    target_id: str,
):
    result_path = get_result_path_for_target(args, target_id)
    use_cache = args.save_result and not args.force_rerun and result_path.exists()

    if use_cache:
        logger.info("Using cached run record for target id=%s from %s", target_id, result_path)
        return load_run_record(result_path)

    record = runner.run_target(
        platform=platform,
        solver=solver,
        target_id=target_id,
        timeout_sec=args.timeout_sec,
    )

    if args.save_result:
        save_run_record(result_path, record)

    return record


if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()

    if args.summary_from_results:
        if args.target_id or args.run_all:
            parser.error("--summary-from-results cannot be combined with --testcase or --run-all")
    elif not args.run_all and not args.target_id:
        parser.error("either --testcase/--target-id, --run-all, or --summary-from-results is required")

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.summary_from_results:
        results_root = Path(args.results_dir) / args.platform / args.solver
        records = load_run_records(results_root)
        summary = summarize_runs(records)
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, default=str))
        raise SystemExit(0)

    platform = create_platform_from_args(args)
    runner = BenchmarkRunner()
    solver = create_solver_from_args(args)

    if args.run_all:
        target_ids = [target.id for target in platform.list_targets()]
        records = [
            run_or_load_record(
                args=args,
                runner=runner,
                platform=platform,
                solver=solver,
                target_id=target_id,
            )
            for target_id in target_ids
        ]
        summary = summarize_runs(records)
        print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2, default=str))
    else:
        record = run_or_load_record(
            args=args,
            runner=runner,
            platform=platform,
            solver=solver,
            target_id=args.target_id,
        )
        print(json.dumps(record.to_dict(), ensure_ascii=False, indent=2, default=str))
