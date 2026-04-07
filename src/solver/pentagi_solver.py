from __future__ import annotations

import json
import logging
import re
import ssl
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any
from urllib import request

from ..result.models import Session, SolveResult
from .base import BaseSolver

logger = logging.getLogger(__name__)


@dataclass
class PentagiClient:
    api_base: str
    user: str
    password: str
    insecure_tls: bool = True

    def __post_init__(self) -> None:
        self.api_base = self.api_base.rstrip("/")
        jar = CookieJar()
        self.ssl_context = ssl._create_unverified_context() if self.insecure_tls else ssl.create_default_context()
        self.opener = request.build_opener(
            request.HTTPCookieProcessor(jar),
            request.HTTPSHandler(context=self.ssl_context),
        )

    def _call(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = request.Request(
            url=f"{self.api_base}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        with self.opener.open(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
        return json.loads(raw)

    def login(self) -> None:
        self._call("POST", "/api/v1/auth/login", {"mail": self.user, "password": self.password})

    def create_flow(self, provider: str, prompt: str) -> int:
        data = self._call("POST", "/api/v1/flows/", {"provider": provider, "input": prompt})
        return int(data["data"]["id"])

    def get_flow(self, flow_id: int) -> dict[str, Any]:
        return self._call("GET", f"/api/v1/flows/{flow_id}")["data"]

    def get_subtasks(self, flow_id: int, page_size: int = 100) -> list[dict[str, Any]]:
        data = self._call("GET", f"/api/v1/flows/{flow_id}/subtasks/?type=init&page=1&pageSize={page_size}")
        return data.get("data", {}).get("subtasks", [])

    def get_msglogs(self, flow_id: int, page_size: int = 100) -> list[dict[str, Any]]:
        data = self._call("GET", f"/api/v1/flows/{flow_id}/msglogs/?type=init&page=1&pageSize={page_size}")
        return data.get("data", {}).get("msglogs", [])

    def send_input(self, flow_id: int, text: str) -> None:
        self._call("PUT", f"/api/v1/flows/{flow_id}", {"action": "input", "input": text})


class PentagiSolver(BaseSolver):
    def __init__(
        self,
        pentagi_api: str = "https://127.0.0.1:8443",
        pentagi_user: str = "admin@pentagi.com",
        pentagi_pass: str = "admin",
        pentagi_provider: str = "custom",
        pentagi_poll_interval_sec: float = 5.0,
        pentagi_max_wait_sec: int = 1800,
        pentagi_insecure_tls: bool = True,
    ) -> None:
        self.pentagi_api = pentagi_api
        self.pentagi_user = pentagi_user
        self.pentagi_pass = pentagi_pass
        self.pentagi_provider = pentagi_provider
        self.poll_interval_sec = pentagi_poll_interval_sec
        self.max_wait_sec = pentagi_max_wait_sec
        self.insecure_tls = pentagi_insecure_tls

    def solve(self, session: Session, submit_flag) -> SolveResult:
        client = PentagiClient(
            api_base=self.pentagi_api,
            user=self.pentagi_user,
            password=self.pentagi_pass,
            insecure_tls=self.insecure_tls,
        )
        client.login()

        prompt = self._build_prompt(session)
        flow_id = client.create_flow(provider=self.pentagi_provider, prompt=prompt)
        logger.info("Created PentAGI flow id=%s for target=%s", flow_id, session.target.id)

        seen_candidates: set[str] = set()
        status_history: list[str] = []
        started = time.monotonic()
        nudges = 0

        while True:
            elapsed = time.monotonic() - started
            if elapsed > self.max_wait_sec:
                logger.warning("PentAGI flow timeout target=%s flow=%s", session.target.id, flow_id)
                return SolveResult(
                    status="timeout",
                    flag=None,
                    stats={"flow_id": flow_id, "status_history": status_history, "elapsed_sec": elapsed},
                )

            flow = client.get_flow(flow_id)
            flow_status = str(flow.get("status", "unknown"))
            status_history.append(flow_status)

            candidates = self._extract_candidates(
                session=session,
                subtasks=client.get_subtasks(flow_id),
                msglogs=client.get_msglogs(flow_id),
            )
            for candidate in candidates:
                if candidate in seen_candidates:
                    continue
                seen_candidates.add(candidate)
                validation = submit_flag(candidate)
                logger.info(
                    "Submitted candidate target=%s flow=%s flag=%s ok=%s",
                    session.target.id,
                    flow_id,
                    candidate,
                    validation.ok,
                )
                if validation.ok:
                    return SolveResult(
                        status="solved",
                        flag=candidate,
                        stats={
                            "flow_id": flow_id,
                            "status_history": status_history,
                            "attempted_candidates": sorted(seen_candidates),
                            "elapsed_sec": elapsed,
                        },
                    )

            if flow_status in {"failed", "finished"}:
                return SolveResult(
                    status="give_up",
                    flag=None,
                    stats={
                        "flow_id": flow_id,
                        "flow_status": flow_status,
                        "status_history": status_history,
                        "attempted_candidates": sorted(seen_candidates),
                        "elapsed_sec": elapsed,
                    },
                )

            if flow_status == "waiting" and nudges < 3:
                nudges += 1
                client.send_input(
                    flow_id,
                    "Continue solving. If you found a candidate flag, output only the final flag string.",
                )

            time.sleep(self.poll_interval_sec)

    def _build_prompt(self, session: Session) -> str:
        normalized_connection = self._normalize_connection_info(session.connection_info)
        metadata = {
            "target_id": session.target.id,
            "target_name": session.target.name,
            "description": session.target.description,
            "files": session.target.files,
            "flag_format": session.target.flag_format,
            "connection_info": normalized_connection,
        }
        extra_notes = self._network_notes(session, normalized_connection)
        return (
            "You are solving a benchmark CTF task autonomously.\n"
            "Use available tools and provided files/connection info.\n"
            "Do not ask human for help.\n"
            "Return only the final flag string once confident.\n\n"
            f"Task metadata:\n{json.dumps(metadata, ensure_ascii=False, indent=2)}\n\n"
            f"{extra_notes}"
        )

    @staticmethod
    def _normalize_connection_info(connection_info: dict[str, Any]) -> dict[str, Any]:
        fixed = dict(connection_info)
        server_name = str(fixed.get("server_name") or "").strip().lower()
        if server_name in {"localhost", "127.0.0.1", "::1"}:
            fixed["server_name"] = "host.docker.internal"
        return fixed

    @staticmethod
    def _network_notes(session: Session, connection_info: dict[str, Any]) -> str:
        notes: list[str] = [
            "Network note:",
            "- PentAGI executes actions inside containers.",
            "- In container context, localhost/127.0.0.1 points to the container itself.",
            "- Prefer host.docker.internal for host-mapped challenge ports.",
        ]
        server_name = str(connection_info.get("server_name") or "").strip()
        port = connection_info.get("port")
        if server_name and port:
            notes.append(f"- Primary target endpoint: http://{server_name}:{port}")

        original_server = str(session.target.metadata.get("original_server_name") or "").strip()
        if original_server:
            notes.append(f"- Original challenge server name (if needed): {original_server}")
        return "\n".join(notes)

    def _extract_candidates(
        self,
        session: Session,
        subtasks: list[dict[str, Any]],
        msglogs: list[dict[str, Any]],
    ) -> list[str]:
        texts: list[str] = []
        for item in subtasks:
            for key in ("result", "title", "description", "context"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    texts.append(value)
        for item in msglogs:
            for key in ("result", "message", "thinking"):
                value = item.get(key)
                if isinstance(value, str) and value:
                    texts.append(value)

        patterns = self._candidate_patterns(session.target.flag_format)
        found: list[str] = []
        for text in texts:
            for pat in patterns:
                for m in pat.findall(text):
                    if isinstance(m, tuple):
                        m = m[0]
                    candidate = str(m).strip().strip("`'\"")
                    if candidate:
                        found.append(candidate)
        # preserve order while deduplicating
        unique: list[str] = []
        seen = set()
        for item in found:
            if item not in seen:
                seen.add(item)
                unique.append(item)
        return unique

    @staticmethod
    def _candidate_patterns(flag_format: str | None) -> list[re.Pattern[str]]:
        patterns: list[str] = []
        if flag_format:
            # turn simple flag format into regex if possible
            esc = re.escape(flag_format)
            esc = esc.replace(r"\{", "{").replace(r"\}", "}")
            if "{" in esc and "}" in esc:
                prefix = esc.split("{", 1)[0]
                patterns.append(rf"{prefix}\{{[^}}\n\r]{{1,200}}\}}")

        patterns.extend(
            [
                r"flag\{[^}\n\r]{1,200}\}",
                r"csawctf\{[^}\n\r]{1,200}\}",
            ]
        )
        return [re.compile(p, flags=re.IGNORECASE) for p in patterns]
