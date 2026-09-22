import ast
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def production_modules():
    for path in SOURCE_ROOT.rglob("*.py"):
        if "tests" not in path.parts and path.name != "main.py":
            yield path


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    return imports


def is_module_or_child(module: str, prefix: str) -> bool:
    return module == prefix or module.startswith(f"{prefix}.")


class LayerDependencyTests(unittest.TestCase):
    def assert_imports_do_not_start_with(
        self,
        path: Path,
        forbidden_prefixes: tuple[str, ...],
    ) -> None:
        violations = sorted(
            imported
            for imported in imported_modules(path)
            if any(
                is_module_or_child(imported, prefix)
                for prefix in forbidden_prefixes
            )
        )
        self.assertEqual(
            violations,
            [],
            f"{path.relative_to(SOURCE_ROOT)} imports forbidden modules",
        )

    def assert_local_imports_within(
        self,
        path: Path,
        allowed_prefixes: tuple[str, ...],
    ) -> None:
        violations = sorted(
            imported
            for imported in imported_modules(path)
            if any(
                is_module_or_child(imported, prefix)
                for prefix in ("agents", "core")
            )
            and not any(
                is_module_or_child(imported, prefix)
                for prefix in allowed_prefixes
            )
        )
        self.assertEqual(
            violations,
            [],
            f"{path.relative_to(SOURCE_ROOT)} imports outside its layer",
        )

    def test_domain_depends_only_on_domain_and_stdlib(self):
        for path in (SOURCE_ROOT / "core" / "models").rglob("*.py"):
            with self.subTest(path=path):
                self.assert_local_imports_within(
                    path,
                    ("core.models",),
                )

    def test_contracts_depend_only_on_domain_contracts_and_stdlib(self):
        for path in (SOURCE_ROOT / "core" / "contracts").rglob("*.py"):
            with self.subTest(path=path):
                self.assert_local_imports_within(
                    path,
                    ("core.contracts", "core.models"),
                )

    def test_agents_depend_only_on_contracts_and_domain(self):
        for path in (SOURCE_ROOT / "agents").rglob("*.py"):
            with self.subTest(path=path):
                self.assert_local_imports_within(
                    path,
                    ("core.contracts", "core.models"),
                )

    def test_application_policies_do_not_import_infrastructure_or_agents(self):
        application_roots = (
            SOURCE_ROOT / "core" / "engines",
            SOURCE_ROOT / "core" / "memory",
            SOURCE_ROOT / "core" / "orchestration",
        )
        for root in application_roots:
            for path in root.rglob("*.py"):
                with self.subTest(path=path):
                    self.assert_local_imports_within(
                        path,
                        ("core.contracts", "core.models"),
                    )

    def test_configuration_depends_only_on_contracts_and_domain(self):
        self.assert_local_imports_within(
            SOURCE_ROOT / "core" / "config.py",
            ("core.contracts", "core.models"),
        )

    def test_only_composition_root_imports_concrete_adapters(self):
        for path in production_modules():
            if "infrastructure" in path.parts:
                continue
            with self.subTest(path=path):
                self.assert_imports_do_not_start_with(
                    path,
                    ("core.infrastructure",),
                )

    def test_infrastructure_depends_only_on_contracts_and_domain(self):
        for path in (SOURCE_ROOT / "core" / "infrastructure").rglob("*.py"):
            with self.subTest(path=path):
                self.assert_local_imports_within(
                    path,
                    ("core.contracts", "core.models"),
                )

    def test_only_composition_root_imports_agents(self):
        for path in production_modules():
            if "agents" in path.parts:
                continue
            with self.subTest(path=path):
                self.assert_imports_do_not_start_with(path, ("agents",))

    def test_no_cross_agent_imports(self):
        for path in (SOURCE_ROOT / "agents").rglob("*.py"):
            with self.subTest(path=path):
                self.assert_imports_do_not_start_with(path, ("agents",))

    def test_agents_do_not_import_filesystem_modules(self):
        for path in (SOURCE_ROOT / "agents").rglob("*.py"):
            with self.subTest(path=path):
                self.assert_imports_do_not_start_with(
                    path,
                    ("os", "pathlib", "shutil", "tempfile"),
                )

    def test_agents_do_not_import_process_or_container_modules(self):
        for path in (SOURCE_ROOT / "agents").rglob("*.py"):
            with self.subTest(path=path):
                self.assert_imports_do_not_start_with(
                    path,
                    ("subprocess", "docker"),
                )


if __name__ == "__main__":
    unittest.main()
