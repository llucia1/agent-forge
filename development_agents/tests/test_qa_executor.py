import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from core.infrastructure.qa_executor import IsolatedProjectQAExecutor
from core.models.architecture import ArchitectureArtifact
from core.models.qa import (
    ExecutableRule,
    QACheck,
    QACheckStatus,
    QACommand,
    QAExecutionPolicy,
    QAValidationError,
)
from core.models.workspace import ProjectSnapshot, WorkspaceFile


class IsolatedProjectQAExecutorTests(unittest.TestCase):
    def setUp(self):
        self.architecture = ArchitectureArtifact.from_json(
            json.dumps(
                {
                    "version": 1,
                    "status": "complete",
                    "missing_decisions": [],
                    "backend_stack": {"runtime": "ServerRuntime"},
                    "backend_architecture": {"style": "ServerStyle"},
                    "frontend_stack": {"runtime": "ClientRuntime"},
                    "frontend_architecture": {"style": "ClientStyle"},
                    "infrastructure": {"runtime": "ProjectRuntime"},
                    "technical_constraints": [],
                    "modules": [],
                    "interfaces": [],
                    "apis": [],
                    "persistence": {},
                    "execution_plan": [],
                }
            )
        )
        self.snapshot = ProjectSnapshot.create(
            self.architecture,
            [WorkspaceFile("backend/project.conf", "check = true\n")],
        )
        self.check = QACheck(
            id="authorized-check",
            command=QACommand(
                executable="quality-tool",
                arguments=("verify",),
                working_directory="backend",
            ),
            evidence_paths=("backend/project.conf",),
            timeout_seconds=10,
        )
        self.policy = QAExecutionPolicy(
            executable_rules=(
                ExecutableRule(
                    executable="quality-tool",
                    allowed_arguments=(("verify",),),
                    allowed_working_directories=("backend",),
                    max_timeout_seconds=20,
                ),
            ),
            max_output_bytes=32,
        )

    @patch("core.infrastructure.qa_executor.shutil.which")
    def test_executes_directly_in_networkless_read_only_sandbox(self, which):
        which.side_effect = lambda executable: {
            "bwrap": "/usr/bin/bwrap",
            "quality-tool": "/usr/bin/quality-tool",
        }.get(executable)
        process_run = Mock(
            return_value=subprocess.CompletedProcess([], 0, b"ok", b"")
        )
        executor = IsolatedProjectQAExecutor(
            self.policy,
            process_run=process_run,
        )

        result = executor.execute(
            self.snapshot,
            (self.check,),
            self.snapshot.fingerprint,
        )

        self.assertEqual(result.checks[0].status, QACheckStatus.PASSED)
        args = process_run.call_args.args[0]
        kwargs = process_run.call_args.kwargs
        self.assertIn("--unshare-all", args)
        self.assertIn("--ro-bind", args)
        self.assertNotIn("shell", kwargs)
        self.assertEqual(
            kwargs["env"],
            {"PATH": "/usr/local/bin:/usr/bin:/bin"},
        )

    @patch("core.infrastructure.qa_executor.shutil.which")
    def test_execution_writes_only_the_temporary_snapshot(self, which):
        which.side_effect = lambda executable: {
            "bwrap": "/usr/bin/bwrap",
            "quality-tool": "/usr/bin/quality-tool",
        }.get(executable)
        with tempfile.TemporaryDirectory() as original_root:
            original = Path(original_root) / "project.conf"
            original.write_text("original\n", encoding="utf-8")

            def execute(command, **kwargs):
                temporary_file = kwargs["cwd"] / "backend" / "project.conf"
                temporary_file.write_text("modified\n", encoding="utf-8")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            executor = IsolatedProjectQAExecutor(
                self.policy,
                process_run=execute,
            )
            executor.execute(
                self.snapshot,
                (self.check,),
                self.snapshot.fingerprint,
            )

            self.assertEqual(
                original.read_text(encoding="utf-8"),
                "original\n",
            )

    def test_revalidates_fingerprint_before_materializing(self):
        executor = IsolatedProjectQAExecutor(self.policy)

        with self.assertRaisesRegex(QAValidationError, "fingerprint"):
            executor.execute(
                self.snapshot,
                (self.check,),
                "sha256:" + "0" * 64,
            )

    def test_compiles_python_snapshot_without_process_or_sandbox(self):
        snapshot = ProjectSnapshot.create(
            self.architecture,
            [WorkspaceFile("backend/main.py", "value = 1\n")],
        )
        check = QACheck(
            id="python-compile",
            command=QACommand(
                executable="python3",
                arguments=("-m", "compileall", "backend"),
                working_directory=".",
            ),
            evidence_paths=("backend/main.py",),
            timeout_seconds=10,
        )
        policy = QAExecutionPolicy(
            (
                ExecutableRule(
                    "python3",
                    (("-m", "compileall", "backend"),),
                    (".",),
                    10,
                ),
            ),
            1024,
        )
        process_run = Mock()

        result = IsolatedProjectQAExecutor(
            policy,
            sandbox_executable="missing-bwrap",
            process_run=process_run,
        ).execute(snapshot, (check,), snapshot.fingerprint)

        self.assertEqual(result.checks[0].status, QACheckStatus.PASSED)
        self.assertEqual(result.checks[0].exit_code, 0)
        process_run.assert_not_called()

    def test_compileall_dot_selects_python_files_from_workspace_root(self):
        snapshot = ProjectSnapshot.create(
            self.architecture,
            [WorkspaceFile("backend/main.py", "value = 1\n")],
        )
        check = QACheck(
            id="python-compile-all",
            command=QACommand(
                executable="python",
                arguments=("-m", "compileall", "."),
                working_directory=".",
            ),
            evidence_paths=("backend/main.py",),
            timeout_seconds=10,
        )
        policy = QAExecutionPolicy(
            (
                ExecutableRule(
                    "python",
                    (("-m", "compileall", "."),),
                    (".",),
                    10,
                ),
            ),
            1024,
        )

        result = IsolatedProjectQAExecutor(
            policy,
            sandbox_executable="missing-bwrap",
        ).execute(snapshot, (check,), snapshot.fingerprint)

        self.assertEqual(result.checks[0].status, QACheckStatus.PASSED)
        self.assertIn("backend/main.py", result.checks[0].stdout_excerpt)

    def test_rejects_shell_operators_and_unlisted_arguments(self):
        unsafe_check = QACheck(
            id=self.check.id,
            command=QACommand(
                executable="quality-tool",
                arguments=("verify", "&&", "other"),
                working_directory="backend",
            ),
            evidence_paths=self.check.evidence_paths,
            timeout_seconds=self.check.timeout_seconds,
        )
        executor = IsolatedProjectQAExecutor(self.policy)

        with self.assertRaisesRegex(QAValidationError, "shell syntax"):
            executor.execute(
                self.snapshot,
                (unsafe_check,),
                self.snapshot.fingerprint,
            )

    def test_rejects_absolute_traversal_and_install_arguments(self):
        for unsafe_argument, message in (
            ("/outside", "unsafe argument path"),
            ("../outside", "unsafe argument path"),
            ("install", "dependency installation"),
        ):
            with self.subTest(argument=unsafe_argument):
                unsafe_check = QACheck(
                    id=self.check.id,
                    command=QACommand(
                        executable="quality-tool",
                        arguments=(unsafe_argument,),
                        working_directory="backend",
                    ),
                    evidence_paths=self.check.evidence_paths,
                    timeout_seconds=self.check.timeout_seconds,
                )
                executor = IsolatedProjectQAExecutor(self.policy)

                with self.assertRaisesRegex(QAValidationError, message):
                    executor.execute(
                        self.snapshot,
                        (unsafe_check,),
                        self.snapshot.fingerprint,
                    )


if __name__ == "__main__":
    unittest.main()
