import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from core.contracts.qa import ProjectQAExecutor
from core.models.architecture import ArchitectureArtifact
from core.models.qa import (
    QACheck,
    QACheckResult,
    QACheckStatus,
    QAExecutionPolicy,
    QAExecutionResult,
    QAValidationError,
    validate_authorized_check,
)
from core.models.workspace import ProjectSnapshot, WorkspaceFile


class QAExecutorError(RuntimeError):
    """Raised when the isolated QA executor cannot operate safely."""


class IsolatedProjectQAExecutor(ProjectQAExecutor):
    def __init__(
        self,
        policy: QAExecutionPolicy,
        sandbox_executable: str = "bwrap",
        process_run=subprocess.run,
    ):
        if policy.network_access:
            raise QAExecutorError(
                "This executor does not permit network access"
            )
        self.policy = policy
        self.sandbox_executable = sandbox_executable
        self.process_run = process_run

    def execute(
        self,
        snapshot: ProjectSnapshot,
        checks: tuple[QACheck, ...],
        expected_fingerprint: str,
    ) -> QAExecutionResult:
        if snapshot.fingerprint != expected_fingerprint:
            raise QAValidationError(
                "QA executor snapshot fingerprint does not match approval"
            )
        validated = [
            (
                check,
                validate_authorized_check(check, snapshot, self.policy),
            )
            for check in checks
        ]

        builtin_results = {
            check.id: self._execute_builtin(
                snapshot,
                check,
                evidence_hashes,
            )
            for check, evidence_hashes in validated
        }
        sandbox_path = shutil.which(self.sandbox_executable)

        with tempfile.TemporaryDirectory(prefix="agentforge-qa-") as root:
            snapshot_root = Path(root) / "workspace"
            snapshot_root.mkdir(mode=0o700)
            self._materialize(snapshot_root, snapshot)
            materialized = self._read_materialized(snapshot_root, snapshot)
            if materialized.fingerprint != expected_fingerprint:
                raise QAValidationError(
                    "Materialized QA snapshot fingerprint does not match "
                    "approval"
                )
            results = [
                builtin_results[check.id]
                or (
                    self._execute_check(
                        sandbox_path,
                        snapshot_root,
                        check,
                        evidence_hashes,
                    )
                    if sandbox_path is not None
                    else self._not_run(
                        check,
                        evidence_hashes,
                        "isolated_executor_unavailable",
                    )
                )
                for check, evidence_hashes in validated
            ]
        return QAExecutionResult(checks=tuple(results))

    def _execute_builtin(
        self,
        snapshot: ProjectSnapshot,
        check: QACheck,
        evidence_hashes: dict[str, str],
    ) -> QACheckResult | None:
        command = check.command
        if command.executable not in ("python", "python3") or (
            len(command.arguments) < 3
            or command.arguments[:2] != ("-m", "compileall")
        ):
            return None

        started = time.monotonic()
        prefix = "" if command.working_directory == "." else (
            command.working_directory + "/"
        )
        available = {
            workspace_file.relative_path: workspace_file.content
            for workspace_file in snapshot.files
        }
        selected = set()
        for raw_target in command.arguments[2:]:
            normalized_target = raw_target.strip("/")
            if normalized_target == ".":
                target = (
                    ""
                    if command.working_directory == "."
                    else command.working_directory
                )
            else:
                target = prefix + normalized_target
            selected.update(
                path
                for path in available
                if path.endswith(".py")
                and (
                    not target
                    or path == target
                    or path.startswith(target + "/")
                )
            )

        errors = []
        for path in sorted(selected):
            try:
                compile(available[path], path, "exec")
            except (SyntaxError, ValueError) as error:
                errors.append(f"{path}: {error}")
        duration_ms = int((time.monotonic() - started) * 1000)
        if not selected:
            errors.append("compileall targets contain no Python files")
        passed = not errors
        stdout, stdout_truncated = self._bounded(
            "Compiled: " + ", ".join(sorted(selected))
            if passed
            else ""
        )
        stderr, stderr_truncated = self._bounded("\n".join(errors))
        return QACheckResult(
            id=check.id,
            status=(
                QACheckStatus.PASSED if passed else QACheckStatus.FAILED
            ),
            exit_code=0 if passed else 1,
            duration_ms=duration_ms,
            stdout_excerpt=stdout,
            stderr_excerpt=stderr,
            output_truncated=stdout_truncated or stderr_truncated,
            failure_reason=None if passed else "compile_error",
            evidence_hashes=evidence_hashes,
        )

    @staticmethod
    def _materialize(root: Path, snapshot: ProjectSnapshot) -> None:
        architecture_path = root / "architecture.json"
        architecture_path.write_text(
            snapshot.architecture.to_json(),
            encoding="utf-8",
        )
        for workspace_file in snapshot.files:
            target = root.joinpath(*workspace_file.relative_path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(workspace_file.content, encoding="utf-8")

    @staticmethod
    def _read_materialized(
        root: Path,
        original: ProjectSnapshot,
    ) -> ProjectSnapshot:
        architecture = ArchitectureArtifact.from_json(
            (root / "architecture.json").read_text(encoding="utf-8")
        )
        files = [
            WorkspaceFile(
                workspace_file.relative_path,
                root.joinpath(
                    *workspace_file.relative_path.split("/")
                ).read_text(encoding="utf-8"),
            )
            for workspace_file in original.files
        ]
        return ProjectSnapshot.create(architecture, files)

    def _execute_check(
        self,
        sandbox_path: str,
        snapshot_root: Path,
        check: QACheck,
        evidence_hashes: dict[str, str],
    ) -> QACheckResult:
        executable_path = shutil.which(check.command.executable)
        if executable_path is None:
            return self._not_run(
                check,
                evidence_hashes,
                "executable_unavailable",
            )
        command = self._sandbox_command(
            sandbox_path,
            snapshot_root,
            executable_path,
            check,
        )
        started = time.monotonic()
        try:
            completed = self.process_run(
                command,
                cwd=snapshot_root,
                env={"PATH": "/usr/local/bin:/usr/bin:/bin"},
                capture_output=True,
                check=False,
                timeout=check.timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout, stdout_truncated = self._bounded(error.stdout or b"")
            stderr, stderr_truncated = self._bounded(error.stderr or b"")
            return QACheckResult(
                id=check.id,
                status=QACheckStatus.FAILED,
                exit_code=None,
                duration_ms=duration_ms,
                stdout_excerpt=stdout,
                stderr_excerpt=stderr,
                output_truncated=stdout_truncated or stderr_truncated,
                failure_reason="timeout",
                evidence_hashes=evidence_hashes,
            )
        duration_ms = int((time.monotonic() - started) * 1000)
        stdout, stdout_truncated = self._bounded(completed.stdout)
        stderr, stderr_truncated = self._bounded(completed.stderr)
        passed = completed.returncode == 0
        return QACheckResult(
            id=check.id,
            status=(
                QACheckStatus.PASSED if passed else QACheckStatus.FAILED
            ),
            exit_code=completed.returncode,
            duration_ms=duration_ms,
            stdout_excerpt=stdout,
            stderr_excerpt=stderr,
            output_truncated=stdout_truncated or stderr_truncated,
            failure_reason=None if passed else "nonzero_exit",
            evidence_hashes=evidence_hashes,
        )

    @staticmethod
    def _sandbox_command(
        sandbox_path: str,
        snapshot_root: Path,
        executable_path: str,
        check: QACheck,
    ) -> list[str]:
        command = [
            sandbox_path,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--clearenv",
        ]
        for system_path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(system_path).exists():
                command.extend(["--ro-bind", system_path, system_path])
        command.extend(
            [
                "--ro-bind",
                str(snapshot_root),
                "/workspace",
                "--tmpfs",
                "/tmp",
                "--proc",
                "/proc",
                "--dev",
                "/dev",
                "--setenv",
                "PATH",
                "/usr/local/bin:/usr/bin:/bin",
                "--chdir",
                (
                    "/workspace"
                    if check.command.working_directory == "."
                    else "/workspace/" + check.command.working_directory
                ),
                executable_path,
                *check.command.arguments,
            ]
        )
        return command

    def _bounded(self, output: bytes | str) -> tuple[str, bool]:
        output_bytes = (
            output.encode("utf-8", errors="replace")
            if isinstance(output, str)
            else output
        )
        truncated = len(output_bytes) > self.policy.max_output_bytes
        bounded = output_bytes[: self.policy.max_output_bytes]
        return bounded.decode("utf-8", errors="replace"), truncated

    @staticmethod
    def _not_run(
        check: QACheck,
        evidence_hashes: dict[str, str],
        reason: str,
    ) -> QACheckResult:
        return QACheckResult(
            id=check.id,
            status=QACheckStatus.NOT_RUN,
            exit_code=None,
            duration_ms=0,
            stdout_excerpt="",
            stderr_excerpt="",
            output_truncated=False,
            failure_reason=reason,
            evidence_hashes=evidence_hashes,
        )
