import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import UUID

from core.contracts.workspaces import (
    ArchitectureArtifactWriter,
    ProjectCodeReader,
    ProjectCodeWorkspace,
    ProjectWorkspaceInitializer,
    WorkspaceBoundaryError,
    WorkspaceError,
)
from core.infrastructure.workspace import FilesystemProjectWorkspace
from core.models.architecture import ArchitectureArtifact
from core.models.workspace import WorkspaceFile


PROJECT_ID = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")


def architecture_artifact() -> ArchitectureArtifact:
    return ArchitectureArtifact.from_json(
        json.dumps(
            {
                "version": 1,
                "status": "complete",
                "missing_decisions": [],
                "backend_stack": {"language": "Python"},
                "backend_architecture": {"style": "layered"},
                "frontend_stack": {"framework": "React"},
                "frontend_architecture": {"style": "components"},
                "infrastructure": {"runtime": "Docker Compose"},
                "technical_constraints": ["Use Python 3.12"],
                "modules": [{"name": "architecture"}],
                "interfaces": [{"name": "WorkspaceWriter"}],
                "apis": [{"name": "tasks"}],
                "persistence": {"database": "PostgreSQL"},
                "execution_plan": [{"step": "Implement contracts"}],
            }
        )
    )


class FilesystemProjectWorkspaceTests(unittest.TestCase):
    def test_implements_narrow_workspace_ports(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = FilesystemProjectWorkspace(Path(root))

        self.assertIsInstance(workspace, ProjectWorkspaceInitializer)
        self.assertIsInstance(workspace, ArchitectureArtifactWriter)
        self.assertIsInstance(workspace, ProjectCodeReader)
        self.assertIsInstance(workspace, ProjectCodeWorkspace)

    def test_initializes_only_the_project_uuid_directory_idempotently(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            workspace = FilesystemProjectWorkspace(root_path)

            workspace.initialize(PROJECT_ID)
            workspace.initialize(PROJECT_ID)

            self.assertEqual(
                list(root_path.iterdir()),
                [root_path / str(PROJECT_ID)],
            )
            self.assertTrue((root_path / str(PROJECT_ID)).is_dir())
            self.assertEqual(
                stat.S_IMODE((root_path / str(PROJECT_ID)).stat().st_mode),
                0o775,
            )

    def test_writes_exact_canonical_json_and_leaves_no_temporary_file(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            workspace = FilesystemProjectWorkspace(root_path)
            artifact = architecture_artifact()

            workspace.write(PROJECT_ID, artifact)

            project_workspace = root_path / str(PROJECT_ID)
            architecture_path = project_workspace / "architecture.json"
            self.assertEqual(
                architecture_path.read_text(encoding="utf-8"),
                artifact.to_json(),
            )
            self.assertEqual(
                list(project_workspace.iterdir()),
                [architecture_path],
            )
            self.assertEqual(
                stat.S_IMODE(architecture_path.stat().st_mode),
                0o664,
            )

    def test_rejects_project_workspace_symlink_outside_root(self):
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as outside:
                root_path = Path(root)
                candidate = root_path / str(PROJECT_ID)
                candidate.symlink_to(Path(outside), target_is_directory=True)
                workspace = FilesystemProjectWorkspace(root_path)

                with self.assertRaises(WorkspaceBoundaryError):
                    workspace.write(PROJECT_ID, architecture_artifact())

                self.assertFalse(
                    (Path(outside) / "architecture.json").exists()
                )

    def test_rejects_non_uuid_identifier_before_resolving_a_path(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = FilesystemProjectWorkspace(Path(root))

            with self.assertRaisesRegex(
                WorkspaceBoundaryError,
                "must be a UUID",
            ):
                workspace.initialize("../outside")

            self.assertEqual(list(Path(root).iterdir()), [])

    def test_requires_an_absolute_existing_workspace_root(self):
        with self.assertRaises(WorkspaceBoundaryError):
            FilesystemProjectWorkspace(Path("workspaces"))

    def test_atomic_replace_failure_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            workspace = FilesystemProjectWorkspace(root_path)

            with patch(
                "core.infrastructure.workspace.os.replace",
                side_effect=OSError("replace failed"),
            ):
                with self.assertRaises(WorkspaceError):
                    workspace.write(PROJECT_ID, architecture_artifact())

            project_workspace = root_path / str(PROJECT_ID)
            self.assertEqual(list(project_workspace.iterdir()), [])

    def test_reads_validated_architecture_or_none(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = FilesystemProjectWorkspace(Path(root))

            self.assertIsNone(workspace.read_architecture(PROJECT_ID))

            expected = architecture_artifact()
            workspace.write(PROJECT_ID, expected)

            self.assertEqual(
                workspace.read_architecture(PROJECT_ID),
                expected,
            )

    def test_reads_only_relevant_text_files_and_excludes_artifacts(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = FilesystemProjectWorkspace(Path(root))
            workspace.write(PROJECT_ID, architecture_artifact())
            project_root = Path(root) / str(PROJECT_ID)

            included = {
                "backend/app.py": "print('backend')\n",
                "backend/settings.toml": "enabled = true\n",
                "Dockerfile": "FROM python:3.12\n",
                ".env.example": "DATABASE_URL=example\n",
                ".gitignore": "dist/\n",
            }
            for relative_path, content in included.items():
                target = project_root / relative_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            excluded_directories = (
                ".git",
                "node_modules",
                "vendor",
                "__pycache__",
                ".cache",
                "cache",
                "caches",
                "build",
                "dist",
                "generated",
                "target",
            )
            for directory_name in excluded_directories:
                target = project_root / directory_name / "ignored.py"
                target.parent.mkdir(parents=True)
                target.write_text("ignored = True\n", encoding="utf-8")

            (project_root / "backend" / "image.png").write_bytes(b"PNG")
            (project_root / "backend" / "binary.py").write_bytes(
                b"valid prefix\x00binary"
            )
            (project_root / "backend" / "latin.py").write_bytes(b"\xff")

            actual = workspace.read_files(PROJECT_ID)

            self.assertEqual(
                {
                    workspace_file.relative_path: workspace_file.content
                    for workspace_file in actual
                },
                included,
            )

    def test_does_not_follow_workspace_symlinks_while_reading(self):
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as outside:
                workspace = FilesystemProjectWorkspace(Path(root))
                workspace.initialize(PROJECT_ID)
                project_root = Path(root) / str(PROJECT_ID)
                outside_path = Path(outside)
                (outside_path / "secret.py").write_text(
                    "SECRET = True\n",
                    encoding="utf-8",
                )
                (project_root / "linked").symlink_to(
                    outside_path,
                    target_is_directory=True,
                )
                (project_root / "secret_link.py").symlink_to(
                    outside_path / "secret.py"
                )

                self.assertEqual(workspace.read_files(PROJECT_ID), [])

    def test_writes_nested_files_atomically_with_host_editable_modes(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = FilesystemProjectWorkspace(Path(root))
            generated_files = [
                WorkspaceFile(
                    "backend/domain/order.py",
                    "class Order:\n    pass\n",
                ),
                WorkspaceFile(
                    "backend/application/create_order.py",
                    "def create_order():\n    pass\n",
                ),
            ]

            workspace.write_files(PROJECT_ID, generated_files)
            workspace.write_files(
                PROJECT_ID,
                [
                    WorkspaceFile(
                        "backend/domain/order.py",
                        "class Order:\n    id: str\n",
                    )
                ],
            )

            project_root = Path(root) / str(PROJECT_ID)
            order_path = project_root / "backend/domain/order.py"
            command_path = (
                project_root / "backend/application/create_order.py"
            )
            self.assertEqual(
                order_path.read_text(encoding="utf-8"),
                "class Order:\n    id: str\n",
            )
            self.assertTrue(command_path.is_file())
            self.assertEqual(
                stat.S_IMODE(order_path.stat().st_mode),
                0o664,
            )
            self.assertEqual(
                stat.S_IMODE(order_path.parent.stat().st_mode),
                0o775,
            )
            self.assertEqual(
                stat.S_IMODE(command_path.stat().st_mode),
                0o664,
            )

    def test_rejects_write_through_symlink_outside_project(self):
        with tempfile.TemporaryDirectory() as root:
            with tempfile.TemporaryDirectory() as outside:
                workspace = FilesystemProjectWorkspace(Path(root))
                workspace.initialize(PROJECT_ID)
                project_root = Path(root) / str(PROJECT_ID)
                (project_root / "backend").symlink_to(
                    Path(outside),
                    target_is_directory=True,
                )

                with self.assertRaises(WorkspaceBoundaryError):
                    workspace.write_files(
                        PROJECT_ID,
                        [WorkspaceFile("backend/escape.py", "unsafe\n")],
                    )

                self.assertFalse((Path(outside) / "escape.py").exists())

    def test_backend_files_cannot_overwrite_architecture_artifact(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = FilesystemProjectWorkspace(Path(root))

            with self.assertRaises(WorkspaceBoundaryError):
                workspace.write_files(
                    PROJECT_ID,
                    [WorkspaceFile("architecture.json", "{}")],
                )

    def test_compose_maps_agent_process_to_configurable_host_identity(self):
        repository_root = Path(__file__).resolve().parents[2]
        compose = (repository_root / "docker-compose.yml").read_text(
            encoding="utf-8"
        )
        example_environment = (
            repository_root / ".env.example"
        ).read_text(encoding="utf-8")

        self.assertIn(
            'user: "${AGENTFORGE_UID:-1000}:${AGENTFORGE_GID:-1000}"',
            compose,
        )
        self.assertIn("AGENTFORGE_UID=1000", example_environment)
        self.assertIn("AGENTFORGE_GID=1000", example_environment)


if __name__ == "__main__":
    unittest.main()
