from __future__ import annotations

import logging
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from nyuctf.challenge import CTFChallenge
from nyuctf.dataset import CTFDataset

from .base import BasePlatform
from ..result.models import Session, Target, ValidationResult

logger = logging.getLogger(__name__)


class NyuPlatform(BasePlatform):
    def __init__(self, split: str = "test", version: str | None = None):
        dataset_kwargs: dict[str, Any] = {"split": split}
        if version is not None:
            dataset_kwargs["version"] = version
        self.dataset = CTFDataset(**dataset_kwargs)
        self.basedir = self.dataset.basedir
        logger.info(
            "Initialized NYU platform split=%s version=%s basedir=%s",
            split,
            version,
            self.basedir,
        )

    def list_targets(self) -> list[Target]:
        targets = [self._to_target(challenge) for challenge in self.dataset.dataset.values()]
        logger.info("Listed %s NYU targets", len(targets))
        return targets

    def get_target(self, target_id: str) -> Target:
        challenge = self.dataset.get(target_id)
        target = self._to_target(challenge)
        logger.info("Loaded target id=%s name=%s", target.id, target.name)
        return target

    def prepare(self, target: Target) -> Session:
        logger.info("Preparing target id=%s", target.id)
        challenge_info = self.dataset.get(target.id)
        challenge = CTFChallenge(challenge_info, self.basedir)
        challenge.start_challenge_container()
        logger.info(
            "Started challenge container for target id=%s server=%s port=%s",
            target.id,
            challenge.server_name,
            challenge.port,
        )
        tempdir = Path(tempfile.mkdtemp(prefix=f"aaagentbench-{target.id}-"))
        files_dir = tempdir / "files"
        files_dir.mkdir(parents=True, exist_ok=True)
        copied_files = self._copy_files(challenge, files_dir)
        logger.info(
            "Prepared temp workspace for target id=%s workdir=%s copied_files=%s",
            target.id,
            tempdir,
            len(copied_files),
        )
        session_target = Target(
            id=target.id,
            name=target.name,
            description=target.description,
            files=copied_files,
            flag_format=target.flag_format,
            metadata=dict(target.metadata),
        )

        connection_info, runtime_network = self._resolve_connection_info(target.id, challenge)

        return Session(
            target=session_target,
            connection_info=connection_info,
            workdir=str(tempdir),
            metadata={
                "challenge": challenge,
                "flag": challenge.flag,
                "container": challenge.container,
                "tempdir": str(tempdir),
                "runtime_network": runtime_network,
            },
        )

    def validate_flag(self, session: Session, flag: str) -> ValidationResult:
        expected_flag = session.metadata["flag"]
        if flag == expected_flag:
            logger.info("Flag validated successfully for target id=%s", session.target.id)
            return ValidationResult(ok=True, message="Correct flag")
        logger.warning("Flag validation failed for target id=%s", session.target.id)
        return ValidationResult(ok=False, message="Incorrect flag")

    def cleanup(self, session: Session) -> None:
        logger.info("Cleaning up target id=%s", session.target.id)
        runtime_network = session.metadata.get("runtime_network")
        if isinstance(runtime_network, dict):
            forwarder_name = str(runtime_network.get("forwarder_name") or "").strip()
            if forwarder_name:
                try:
                    subprocess.run(
                        ["docker", "rm", "-f", forwarder_name],
                        check=False,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                    logger.info("Removed forwarder container target id=%s name=%s", session.target.id, forwarder_name)
                except Exception:
                    logger.warning(
                        "Failed to remove forwarder container target id=%s name=%s",
                        session.target.id,
                        forwarder_name,
                    )
        challenge = session.metadata.get("challenge")
        if challenge is not None:
            challenge.stop_challenge_container()
            logger.info("Stopped challenge container for target id=%s", session.target.id)
        tempdir = session.metadata.get("tempdir")
        if tempdir:
            shutil.rmtree(tempdir, ignore_errors=True)
            logger.info("Removed temp workspace for target id=%s workdir=%s", session.target.id, tempdir)

    def _to_target(self, challenge_info: dict[str, Any]) -> Target:
        challenge = CTFChallenge(challenge_info, self.basedir)
        files = [str(Path(challenge.challenge_dir) / file_name) for file_name in challenge.files]
        description = self._rewrite_server_name(challenge.description, challenge.server_name)

        return Target(
            id=challenge.canonical_name,
            name=challenge.name,
            description=description,
            files=files,
            flag_format=challenge.flag_format,
            metadata={
                "year": challenge.year,
                "event": challenge.event,
                "category": challenge.category,
                "container": challenge.container,
                "server_name": "localhost" if challenge.server_name else None,
                "original_server_name": challenge.server_name,
                "port": challenge.port,
                "challenge_path": str(challenge.challenge_dir),
            },
        )

    @staticmethod
    def _rewrite_server_name(description: str, server_name: str | None) -> str:
        if not server_name:
            return description
        return description.replace(server_name, "localhost")

    @staticmethod
    def _copy_files(challenge: CTFChallenge, destination_dir: Path) -> list[str]:
        copied_files: list[str] = []
        for file_name in challenge.files:
            source = Path(challenge.challenge_dir) / file_name
            relative_path = Path(file_name)
            destination = destination_dir / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            logger.debug("Copying challenge file source=%s destination=%s", source, destination)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
            copied_files.append(str(destination))
        return copied_files

    def _resolve_connection_info(self, target_id: str, challenge: CTFChallenge) -> tuple[dict[str, Any], dict[str, Any]]:
        connection_info: dict[str, Any] = {
            "server_name": "localhost" if challenge.server_name else None,
            "port": challenge.port,
            "server_type": challenge.server_type,
        }
        runtime_network: dict[str, Any] = {}
        if challenge.server_type != "web" or not challenge.server_name or not challenge.port:
            return connection_info, runtime_network

        compose_path = Path(challenge.challenge_dir) / "docker-compose.yml"
        if not compose_path.exists():
            return connection_info, runtime_network

        service_name = self._compose_primary_service(compose_path)
        if not service_name:
            return connection_info, runtime_network

        mapped = self._compose_published_port(compose_path, service_name, int(challenge.port))
        if mapped:
            connection_info["port"] = mapped
            runtime_network["resolved_via"] = "compose_port"
            runtime_network["compose_service"] = service_name
            logger.info(
                "Resolved host port via compose target id=%s service=%s container_port=%s host_port=%s",
                target_id,
                service_name,
                challenge.port,
                mapped,
            )
            return connection_info, runtime_network

        forwarder = self._start_forwarder(
            target_id=target_id,
            upstream_host=challenge.server_name,
            upstream_port=int(challenge.port),
        )
        if forwarder:
            connection_info["server_name"] = "127.0.0.1"
            connection_info["port"] = forwarder["host_port"]
            runtime_network.update(forwarder)
            runtime_network["resolved_via"] = "forwarder"
            runtime_network["compose_service"] = service_name
            logger.info(
                "Resolved web endpoint via forwarder target id=%s upstream=%s:%s local=127.0.0.1:%s",
                target_id,
                challenge.server_name,
                challenge.port,
                forwarder["host_port"],
            )
        return connection_info, runtime_network

    @staticmethod
    def _compose_primary_service(compose_path: Path) -> str:
        try:
            out = subprocess.check_output(
                ["docker", "compose", "-f", str(compose_path), "config", "--services"],
                text=True,
            )
        except Exception:
            return ""
        for line in out.splitlines():
            name = line.strip()
            if name:
                return name
        return ""

    @staticmethod
    def _compose_published_port(compose_path: Path, service_name: str, container_port: int) -> int | None:
        try:
            out = subprocess.check_output(
                ["docker", "compose", "-f", str(compose_path), "port", service_name, str(container_port)],
                text=True,
                stderr=subprocess.STDOUT,
            )
        except Exception:
            return None
        for line in out.splitlines():
            text = line.strip()
            if not text:
                continue
            match = re.search(r":(\d+)$", text)
            if not match:
                continue
            try:
                return int(match.group(1))
            except ValueError:
                continue
        return None

    @staticmethod
    def _start_forwarder(target_id: str, upstream_host: str, upstream_port: int) -> dict[str, Any]:
        safe_target = re.sub(r"[^a-zA-Z0-9_.-]", "-", target_id).lower()
        suffix = uuid.uuid4().hex[:8]
        name = f"aaabench-fw-{safe_target}-{suffix}"[:63]
        listen_port = upstream_port
        run_cmd = [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            name,
            "--network",
            "ctfnet",
            "-p",
            f"127.0.0.1::{listen_port}",
            "alpine/socat",
            "-d",
            "-d",
            f"TCP-LISTEN:{listen_port},fork,reuseaddr",
            f"TCP:{upstream_host}:{upstream_port}",
        ]
        try:
            subprocess.check_output(run_cmd, text=True, stderr=subprocess.STDOUT)
            port_out = subprocess.check_output(
                ["docker", "port", name, f"{listen_port}/tcp"],
                text=True,
                stderr=subprocess.STDOUT,
            )
        except Exception as exc:
            logger.warning(
                "Failed to start forwarder for target id=%s upstream=%s:%s error=%s",
                target_id,
                upstream_host,
                upstream_port,
                exc,
            )
            try:
                subprocess.run(
                    ["docker", "rm", "-f", name],
                    check=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except Exception:
                pass
            return {}

        host_port = None
        for line in port_out.splitlines():
            match = re.search(r":(\d+)$", line.strip())
            if not match:
                continue
            try:
                host_port = int(match.group(1))
                break
            except ValueError:
                continue
        if not host_port:
            subprocess.run(
                ["docker", "rm", "-f", name],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            return {}
        return {
            "forwarder_name": name,
            "forwarder_upstream_host": upstream_host,
            "forwarder_upstream_port": upstream_port,
            "host_port": host_port,
        }
