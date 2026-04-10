from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from ..result.models import Session, SolveResult
from .base import BaseSolver

logger = logging.getLogger(__name__)


class MakoSolver(BaseSolver):
    def __init__(
        self,
        mako_root: str = "../mako",
        mako_env: str = ".env",
        mako_index: str = "rag_data/index.jsonl",
        max_attempts: int = 3,
        mako_max_steps: int = 12,
        mako_cmd_timeout: int = 25,
        mako_worker_mode: str = "threaded",
    ) -> None:
        self.mako_root = Path(mako_root).expanduser().resolve()
        self.mako_env = mako_env
        self.mako_index = mako_index
        self.max_attempts = max_attempts
        self.mako_max_steps = mako_max_steps
        self.mako_cmd_timeout = mako_cmd_timeout
        self.mako_worker_mode = mako_worker_mode

    def solve(self, session: Session, submit_flag) -> SolveResult:
        feedback: str | None = None
        attempt_summaries: list[dict[str, Any]] = []

        if not (self.mako_root / "web_agent" / "cmd_agent.py").exists():
            raise RuntimeError(f"mako repo not found or invalid: {self.mako_root}")

        target_url = self._target_url(session.connection_info)
        if not target_url:
            return SolveResult(
                status="give_up",
                flag=None,
                stats={"reason": "missing_target_url"},
            )

        logger.info(
            "Starting Mako solve target=%s target_url=%s max_attempts=%s",
            session.target.id,
            target_url,
            self.max_attempts,
        )

        for attempt in range(1, self.max_attempts + 1):
            result = self._run_mako(session=session, target_url=target_url, attempt=attempt, feedback=feedback)
            attempt_summaries.append(result)

            flag = str(result.get("flag") or "").strip()
            if not flag:
                logger.info("Mako attempt=%s returned no flag for target=%s", attempt, session.target.id)
                feedback = f"No valid flag extracted in attempt {attempt}. Try a different strategy and validate final flag format."
                continue

            validation = submit_flag(flag)
            logger.info(
                "Submitted Mako candidate target=%s attempt=%s flag=%s ok=%s",
                session.target.id,
                attempt,
                flag,
                validation.ok,
            )
            if validation.ok:
                return SolveResult(
                    status="solved",
                    flag=flag,
                    stats={
                        "attempts": attempt,
                        "mako_attempts": attempt_summaries,
                        "target_url": target_url,
                    },
                )

            feedback = (
                f"Previous candidate was rejected: {flag}. "
                f"Validation message: {validation.message}. "
                "Continue from current understanding and output only correct final flag."
            )

        return SolveResult(
            status="give_up",
            flag=None,
            stats={
                "attempts": self.max_attempts,
                "mako_attempts": attempt_summaries,
                "target_url": target_url,
            },
        )

    def _run_mako(self, session: Session, target_url: str, attempt: int, feedback: str | None) -> dict[str, Any]:
        bench_workdir = Path(session.workdir)
        out_path = bench_workdir / f"mako_output_{attempt}.json"
        artifact_dir = bench_workdir / "mako_artifacts" / f"attempt_{attempt}"
        memory_db = bench_workdir / "mako_memory.sqlite"

        objective = f"Solve NYU challenge {session.target.id} and retrieve final flag"
        hint = "blackbox web challenge"
        if session.target.description:
            hint = session.target.description.strip().replace("\n", " ")[:400]
        if feedback:
            hint = f"{hint} | feedback: {feedback[:280]}"

        cmd = [
            "python3",
            "-m",
            "web_agent.cmd_agent",
            "--root",
            str(self.mako_root),
            "--env",
            self.mako_env,
            "--index",
            self.mako_index,
            "--target",
            target_url,
            "--objective",
            objective,
            "--hint",
            hint,
            "--max-steps",
            str(self.mako_max_steps),
            "--cmd-timeout",
            str(self.mako_cmd_timeout),
            "--worker-mode",
            self.mako_worker_mode,
            "--memory-db",
            str(memory_db),
            "--out",
            str(out_path),
            "--artifact-dir",
            str(artifact_dir),
            "--run-id",
            f"aaabench_{session.target.id}_attempt{attempt}",
        ]

        logger.debug("Running mako command: %s", " ".join(cmd))
        completed = subprocess.run(
            cmd,
            cwd=self.mako_root,
            text=True,
            capture_output=True,
            check=False,
        )

        if completed.returncode != 0:
            raise RuntimeError(
                "Mako execution failed.\n"
                f"returncode={completed.returncode}\n"
                f"stdout_tail={completed.stdout[-3000:]}\n"
                f"stderr_tail={completed.stderr[-3000:]}"
            )

        if not out_path.exists():
            raise RuntimeError(f"Mako did not produce output file: {out_path}")

        payload = json.loads(out_path.read_text(encoding="utf-8"))
        final_report = str(payload.get("final_report") or "")
        raw_flag = str(payload.get("flag") or "").strip()
        candidate = raw_flag or self._extract_candidate(final_report, session.target.flag_format)

        return {
            "attempt": attempt,
            "flag": candidate,
            "raw_flag": raw_flag,
            "done": bool(payload.get("done", False)),
            "final_report": final_report[:1200],
            "output_path": str(out_path),
            "artifact_dir": str(artifact_dir),
        }

    @staticmethod
    def _target_url(connection_info: dict[str, Any]) -> str:
        host = str(connection_info.get("server_name") or "localhost").strip() or "localhost"
        port = connection_info.get("port")
        if not port:
            return ""
        return f"http://{host}:{port}/"

    @staticmethod
    def _extract_candidate(text: str, flag_format: str | None) -> str:
        patterns: list[re.Pattern[str]] = []
        if flag_format:
            esc = re.escape(flag_format)
            esc = esc.replace(r"\{", "{").replace(r"\}", "}")
            if "{" in esc and "}" in esc:
                prefix = esc.split("{", 1)[0]
                patterns.append(re.compile(rf"{prefix}\{{[^}}\\n\\r]{{1,200}}\}}", re.IGNORECASE))

        patterns.extend(
            [
                re.compile(r"flag\{[^}\n\r]{1,200}\}", re.IGNORECASE),
                re.compile(r"csawctf\{[^}\n\r]{1,200}\}", re.IGNORECASE),
            ]
        )

        for pattern in patterns:
            m = pattern.search(text)
            if m:
                return m.group(0).strip().strip("`'\"")
        return ""
