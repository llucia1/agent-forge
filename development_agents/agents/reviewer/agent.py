import ast
import builtins
import json
import posixpath
import re
import symtable
import sys
from typing import Any

from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.messaging import TaskHandler
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ProjectCodeReader
from core.models.architecture import ArchitectureArtifact, ArchitectureStatus
from core.models.project import Project
from core.models.review import (
    ReviewArtifact,
    ReviewFinding,
    ReviewStatus,
    ReviewValidationError,
    TechnicalBaseline,
)
from core.models.task import Task, TaskStatus
from core.models.task_result import TaskExecutionResult
from core.models.workspace import ProjectSnapshot, WorkspaceFile


class ReviewProjectNotFoundError(LookupError):
    """Raised when a review task references an unknown project."""


ARCHITECTURE_AUTHORITATIVE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
    "technical_constraints",
)
REQUIRED_TECHNICAL_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
)
DEPENDENCY_KEYS = frozenset(
    {
        "dependencies",
        "framework",
        "frameworks",
        "libraries",
        "library",
        "orm",
        "package",
        "packages",
        "persistence_library",
        "database_driver",
    }
)
JAVASCRIPT_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
SOURCE_SUFFIXES = (".py", *JAVASCRIPT_SUFFIXES)
PLACEHOLDER_MARKERS = (
    "todo",
    "fixme",
    "placeholder",
    "example usage",
    "replace with actual",
)
MAX_REVIEW_ATTEMPTS = 3


class ReviewerAgent(TaskHandler):
    def __init__(
        self,
        task_status_writer: TaskStatusWriter,
        task_result_writer: TaskResultWriter,
        project_reader: ProjectReader,
        workspace_reader: ProjectCodeReader,
        engine: AgentEngine,
        context_provider: ContextProvider,
    ):
        self.task_status_writer = task_status_writer
        self.task_result_writer = task_result_writer
        self.project_reader = project_reader
        self.workspace_reader = workspace_reader
        self.engine = engine
        self.context_provider = context_provider

    def handle(self, task: Task) -> None:
        try:
            self._update_status(task, TaskStatus.IN_PROGRESS)
            project = self.project_reader.find_by_id(task.project_id)
            if project is None:
                raise ReviewProjectNotFoundError(
                    f"Project not found: {task.project_id}"
                )

            architecture = self.workspace_reader.read_architecture(
                task.project_id
            )
            if architecture is None:
                self._persist_needs_input(
                    task,
                    project,
                    ["architecture_artifact"],
                )
                return

            self._validate_architecture_authority(project, architecture)
            if architecture.status is ArchitectureStatus.NEEDS_INPUT:
                self._persist_needs_input(
                    task,
                    project,
                    list(architecture.missing_decisions),
                )
                return

            functional_contract = self._read_functional_contract(
                task,
                project,
            )
            if task.spec_path is not None and functional_contract is None:
                return

            missing_decisions = [
                field_name
                for field_name in REQUIRED_TECHNICAL_FIELDS
                if not getattr(project, field_name)
            ]
            if missing_decisions:
                self._persist_needs_input(
                    task,
                    project,
                    missing_decisions,
                )
                return

            workspace_files = self.workspace_reader.read_files(
                task.project_id
            )
            backend_files, frontend_files, shared_files = (
                self._classify_files(workspace_files)
            )
            rule_catalog = self._rule_catalog(
                project,
                architecture,
                backend_files,
                frontend_files,
                functional_contract,
            )
            expected_findings = self._inspect_workspace(
                project,
                architecture,
                workspace_files,
            )
            expected_status = (
                ReviewStatus.CHANGES_REQUIRED
                if expected_findings
                else ReviewStatus.APPROVED
            )

            validation_feedback = None
            for attempt in range(1, MAX_REVIEW_ATTEMPTS + 1):
                engine_result = self.process(
                    task,
                    project,
                    architecture,
                    backend_files,
                    frontend_files,
                    shared_files,
                    functional_contract,
                    rule_catalog,
                    expected_status,
                    expected_findings,
                    validation_feedback,
                )
                try:
                    review = ReviewArtifact.from_json(engine_result.output)
                    self._validate_review_authority(
                        project,
                        review,
                        rule_catalog,
                        workspace_files,
                        expected_status,
                        expected_findings,
                    )
                    break
                except ReviewValidationError as error:
                    if attempt == MAX_REVIEW_ATTEMPTS:
                        raise
                    validation_feedback = {
                        "attempt": attempt,
                        "validation_error": str(error),
                        "instruction": (
                            "Return the complete review again. Copy the "
                            "deterministic status and findings exactly; do "
                            "not add, remove or reinterpret evidence."
                        ),
                    }

            result_metadata = {
                **engine_result.metadata,
                "workspace_fingerprint": ProjectSnapshot.create(
                    architecture,
                    workspace_files,
                ).fingerprint,
                "deterministic_findings": len(expected_findings),
            }
            if functional_contract is not None:
                result_metadata.update(
                    {
                        "spec_path": task.spec_path,
                        "spec_fingerprint": functional_contract[
                            "spec_fingerprint"
                        ],
                    }
                )
            engine_result = EngineResult(
                output=engine_result.output,
                metadata=result_metadata,
                provider=engine_result.provider,
                model_alias=engine_result.model_alias,
            )
            self._persist_result(task, engine_result, review)
            self._update_status(task, TaskStatus.COMPLETED)
        except Exception:
            try:
                self._update_status(task, TaskStatus.FAILED)
            except Exception:
                pass
            raise

    def process(
        self,
        task: Task,
        project: Project,
        architecture: ArchitectureArtifact,
        backend_files: list[WorkspaceFile],
        frontend_files: list[WorkspaceFile],
        shared_files: list[WorkspaceFile],
        functional_contract: dict[str, Any] | None,
        rule_catalog: list[dict[str, Any]],
        expected_status: ReviewStatus,
        expected_findings: list[ReviewFinding],
        validation_feedback: dict[str, Any] | None = None,
    ) -> EngineResult:
        context = {
            **self.context_provider.build(task, project),
            "output_contract": self._output_contract(
                project,
                rule_catalog,
                expected_status,
                expected_findings,
            ),
            "review": {
                "rule_catalog": rule_catalog,
                "architecture_artifact": architecture.to_dict(),
                "backend_files": self._serialize_files(backend_files),
                "frontend_files": self._serialize_files(frontend_files),
                "shared_files": self._serialize_files(shared_files),
                "functional_contract": functional_contract,
                "deterministic_status": str(expected_status),
                "deterministic_findings": [
                    finding.to_dict() for finding in expected_findings
                ],
            },
        }
        if validation_feedback is not None:
            context["review_validation_feedback"] = validation_feedback
        return self.engine.run(task=task, project=project, context=context)

    def _read_functional_contract(
        self,
        task: Task,
        project: Project,
    ) -> dict[str, Any] | None:
        if task.spec_path is None:
            return None
        project_spec = self.workspace_reader.read_project_specification(
            task.project_id
        )
        specification = self.workspace_reader.read_specification(
            task.project_id,
            task.spec_path,
        )
        missing = []
        if project_spec is None:
            missing.append("project_specification")
        if specification is None:
            missing.append("feature_specification")
        if missing:
            self._persist_needs_input(task, project, missing)
            return None
        return {
            "project_markdown": project_spec.markdown,
            "spec_path": task.spec_path,
            "spec_markdown": specification.markdown,
            "spec_fingerprint": specification.fingerprint,
            "acceptance_criteria": [
                {
                    "id": criterion.id,
                    "check_id": criterion.check_id,
                    "description": criterion.description,
                }
                for criterion in specification.acceptance_criteria
            ],
        }

    def _persist_needs_input(
        self,
        task: Task,
        project: Project,
        missing_decisions: list[str],
    ) -> None:
        review = ReviewArtifact(
            version=1,
            status=ReviewStatus.NEEDS_INPUT,
            summary="Project review requires additional inputs",
            technical_baseline=self._technical_baseline(project),
            findings=[],
            checked_rules=[],
            missing_decisions=missing_decisions,
        )
        result = EngineResult(
            output=review.to_json(),
            metadata={"missing_decisions": missing_decisions},
            provider="agentforge",
            model_alias=None,
        )
        self._persist_result(task, result, review)
        self._update_status(task, TaskStatus.NEEDS_INPUT)

    def _persist_result(
        self,
        task: Task,
        engine_result: EngineResult,
        review: ReviewArtifact,
    ) -> None:
        self.task_result_writer.write(
            TaskExecutionResult(
                task_id=task.id,
                project_id=task.project_id,
                agent=str(task.agent),
                provider=engine_result.provider,
                model_alias=engine_result.model_alias,
                output=review.to_json(),
                metadata=dict(engine_result.metadata),
            )
        )

    @staticmethod
    def _technical_baseline(project: Project) -> TechnicalBaseline:
        return TechnicalBaseline(
            backend_stack=dict(project.backend_stack),
            backend_architecture=dict(project.backend_architecture),
            frontend_stack=dict(project.frontend_stack),
            frontend_architecture=dict(project.frontend_architecture),
            infrastructure=dict(project.infrastructure),
            technical_constraints=list(project.technical_constraints),
        )

    @classmethod
    def _output_contract(
        cls,
        project: Project,
        rule_catalog: list[dict[str, Any]],
        expected_status: ReviewStatus,
        expected_findings: list[ReviewFinding],
    ) -> dict[str, Any]:
        contract = ReviewArtifact.output_contract()
        baseline = cls._technical_baseline(project).to_dict()
        checked_rules = [rule["id"] for rule in rule_catalog]
        properties = contract["properties"]
        properties["technical_baseline"] = {
            "type": "object",
            "const": baseline,
        }
        properties["checked_rules"] = {
            "type": "array",
            "const": checked_rules,
        }
        properties["status"] = {
            "type": "string",
            "const": str(expected_status),
        }
        properties["findings"] = {
            "type": "array",
            "const": [finding.to_dict() for finding in expected_findings],
        }
        properties["missing_decisions"] = {
            "type": "array",
            "const": [],
        }
        properties["summary"]["minLength"] = 1
        contract["instruction"] = (
            f"{contract['instruction']} Static inspection has already "
            "established the authoritative status and findings. Copy them "
            "exactly. Do not invent, omit, merge, reword or reprioritize "
            "findings. Summarize only that supplied evidence."
        )
        return contract

    @classmethod
    def _rule_catalog(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
        backend_files: list[WorkspaceFile],
        frontend_files: list[WorkspaceFile],
        functional_contract: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        rules = [
            {"id": "project.backend_stack", "value": project.backend_stack},
            {
                "id": "project.backend_architecture",
                "value": project.backend_architecture,
            },
            {
                "id": "project.frontend_stack",
                "value": project.frontend_stack,
            },
            {
                "id": "project.frontend_architecture",
                "value": project.frontend_architecture,
            },
            {"id": "project.infrastructure", "value": project.infrastructure},
            {
                "id": "project.technical_constraints",
                "value": project.technical_constraints,
            },
            {"id": "architecture.modules", "value": architecture.modules},
            {
                "id": "architecture.interfaces",
                "value": architecture.interfaces,
            },
            {"id": "architecture.apis", "value": architecture.apis},
            {
                "id": "architecture.persistence",
                "value": architecture.persistence,
            },
            {
                "id": "architecture.execution_plan",
                "value": architecture.execution_plan,
            },
            {
                "id": "implementation.backend",
                "value": [item.relative_path for item in backend_files],
            },
            {
                "id": "implementation.frontend",
                "value": [item.relative_path for item in frontend_files],
            },
            {
                "id": "integration.backend_frontend",
                "value": {
                    "apis": architecture.apis,
                    "interfaces": architecture.interfaces,
                },
            },
        ]
        if functional_contract is not None:
            rules.append(
                {
                    "id": "spec.functional_contract",
                    "value": functional_contract,
                }
            )
        return rules

    @staticmethod
    def _classify_files(
        workspace_files: list[WorkspaceFile],
    ) -> tuple[list[WorkspaceFile], list[WorkspaceFile], list[WorkspaceFile]]:
        backend_files = []
        frontend_files = []
        shared_files = []
        for workspace_file in workspace_files:
            if workspace_file.relative_path.startswith("backend/"):
                backend_files.append(workspace_file)
            elif workspace_file.relative_path.startswith("frontend/"):
                frontend_files.append(workspace_file)
            else:
                shared_files.append(workspace_file)
        return backend_files, frontend_files, shared_files

    @staticmethod
    def _serialize_files(
        workspace_files: list[WorkspaceFile],
    ) -> list[dict[str, str]]:
        return [
            {"path": item.relative_path, "content": item.content}
            for item in workspace_files
        ]

    @staticmethod
    def _validate_architecture_authority(
        project: Project,
        architecture: ArchitectureArtifact,
    ) -> None:
        mismatches = [
            field_name
            for field_name in ARCHITECTURE_AUTHORITATIVE_FIELDS
            if getattr(architecture, field_name) != getattr(project, field_name)
        ]
        if mismatches:
            raise ReviewValidationError(
                "Architecture artifact contradicts Project fields: "
                + ", ".join(mismatches)
            )

    @classmethod
    def _validate_review_authority(
        cls,
        project: Project,
        review: ReviewArtifact,
        rule_catalog: list[dict[str, Any]],
        workspace_files: list[WorkspaceFile],
        expected_status: ReviewStatus,
        expected_findings: list[ReviewFinding],
    ) -> None:
        if review.technical_baseline != cls._technical_baseline(project):
            raise ReviewValidationError(
                "Review technical_baseline contradicts Project"
            )
        expected_rules = [rule["id"] for rule in rule_catalog]
        if review.checked_rules != expected_rules:
            raise ReviewValidationError(
                "Review checked_rules does not match the rule catalog"
            )
        if review.status is not expected_status:
            raise ReviewValidationError(
                "Review status contradicts deterministic inspection"
            )
        if review.missing_decisions:
            raise ReviewValidationError(
                "Completed inspection cannot report missing decisions"
            )
        if review.findings != expected_findings:
            raise ReviewValidationError(
                "Review findings contradict deterministic inspection"
            )
        available_paths = {item.relative_path for item in workspace_files}
        if any(
            finding.path is not None and finding.path not in available_paths
            for finding in review.findings
        ):
            raise ReviewValidationError(
                "Review findings reference unread workspace paths"
            )

    @classmethod
    def _inspect_workspace(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
        workspace_files: list[WorkspaceFile],
    ) -> list[ReviewFinding]:
        files = {item.relative_path: item.content for item in workspace_files}
        findings: list[ReviewFinding] = []
        findings.extend(_inspect_modules(architecture, files))
        findings.extend(_inspect_placeholders(files))
        findings.extend(_inspect_technologies(project, files))
        findings.extend(_inspect_dependencies(project, files))
        findings.extend(_inspect_python_references(files, project))
        findings.extend(_inspect_javascript_references(files, project))
        findings.extend(_inspect_contracts(architecture, files))
        findings.extend(_inspect_persistence(architecture, files))
        return sorted(_deduplicate_findings(findings), key=lambda item: item.id)

    def _update_status(self, task: Task, status: TaskStatus) -> None:
        self.task_status_writer.update_status(task.id, status)
        task.status = status


def _finding(
    finding_id: str,
    severity: str,
    scope: str,
    path: str | None,
    rule: str,
    message: str,
    suggested_action: str,
) -> ReviewFinding:
    return ReviewFinding.from_dict(
        {
            "id": finding_id,
            "severity": severity,
            "scope": scope,
            "path": path,
            "rule": rule,
            "message": message,
            "suggested_action": suggested_action,
        },
        0,
    )


def _module_scope(module: dict[str, Any]) -> str:
    name = module["name"].casefold()
    layer = module["layer"].casefold()
    if "front" in name or layer in {"presentation", "ui", "client"}:
        return "frontend"
    if "back" in name or layer in {
        "application",
        "domain",
        "infrastructure",
        "server",
    }:
        return "backend"
    return name.replace("_", "-")


def _module_files(
    module: dict[str, Any],
    files: dict[str, str],
) -> list[str]:
    scope = _module_scope(module)
    module_name = module["name"].casefold().replace("_", "-")
    return [
        path
        for path in files
        if path.casefold().startswith(f"{scope}/")
        or path.casefold().startswith(f"{module_name}/")
    ]


def _inspect_modules(
    architecture: ArchitectureArtifact,
    files: dict[str, str],
) -> list[ReviewFinding]:
    findings = []
    for module in architecture.modules:
        if _module_files(module, files):
            continue
        module_name = module["name"]
        findings.append(
            _finding(
                f"REV-MODULE-MISSING-{_slug(module_name)}",
                "high",
                "architecture",
                None,
                "architecture.modules",
                f"Declared module {module_name} has no workspace implementation",
                f"Implement the files and responsibilities of {module_name}",
            )
        )
    return findings


def _inspect_placeholders(files: dict[str, str]) -> list[ReviewFinding]:
    findings = []
    for path, content in files.items():
        if not path.endswith(SOURCE_SUFFIXES):
            continue
        comments = re.findall(r"//[^\n]*|/\*.*?\*/|#[^\n]*", content, re.DOTALL)
        has_marker = any(
            marker in comment.casefold()
            for comment in comments
            for marker in PLACEHOLDER_MARKERS
        )
        has_fake_id = re.search(
            r"[\"'](?:example|sample|dummy)(?:[-_][A-Za-z0-9]+)*[-_]id[\"']",
            content,
            re.IGNORECASE,
        )
        incomplete = bool(
            re.search(
                r"throw\s+new\s+Error\s*\(\s*[\"'](?:not implemented|todo)",
                content,
                re.IGNORECASE,
            )
        )
        if path.endswith(".py"):
            try:
                tree = ast.parse(content)
            except SyntaxError:
                tree = None
            if tree is not None:
                incomplete = incomplete or any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and (
                        not node.body
                        or all(isinstance(item, ast.Pass) for item in node.body)
                        or any(_raises_not_implemented(item) for item in node.body)
                    )
                    for node in ast.walk(tree)
                )
        if has_marker or has_fake_id or incomplete:
            scope = "backend" if path.startswith("backend/") else "frontend"
            findings.append(
                _finding(
                    f"REV-PLACEHOLDER-{_slug(path)}",
                    "high",
                    scope,
                    path,
                    f"implementation.{scope}",
                    "Source contains placeholder or incomplete implementation",
                    "Replace placeholder code with a complete implementation",
                )
            )
    return findings


def _inspect_technologies(
    project: Project,
    files: dict[str, str],
) -> list[ReviewFinding]:
    findings = []
    backend_language = str(project.backend_stack.get("language", "")).casefold()
    frontend_language = str(project.frontend_stack.get("language", "")).casefold()
    for path in files:
        suffix = posixpath.splitext(path)[1].casefold()
        violation = False
        rule = ""
        scope = ""
        if path.startswith("backend/") and suffix in SOURCE_SUFFIXES:
            scope = "backend"
            rule = "project.backend_stack"
            violation = backend_language == "python" and suffix != ".py"
        elif path.startswith("frontend/") and suffix in SOURCE_SUFFIXES:
            scope = "frontend"
            rule = "project.frontend_stack"
            if frontend_language == "typescript":
                violation = suffix not in {".ts", ".tsx"}
            elif frontend_language in {"javascript", "ecmascript"}:
                violation = suffix not in {".js", ".jsx", ".mjs", ".cjs"}
        if violation:
            findings.append(
                _finding(
                    f"REV-TECH-{_slug(path)}",
                    "high",
                    scope,
                    path,
                    rule,
                    "Source technology is not authorized by Project stack",
                    "Rewrite the file using the declared project language",
                )
            )
    return findings


def _declared_dependencies(stack: dict[str, Any]) -> set[str]:
    dependencies: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            name = _dependency_name(value)
            if name and name not in {"none", "vanilla", "standard-library"}:
                dependencies.append(name)
        elif isinstance(value, list):
            for nested in value:
                collect(nested)
        elif isinstance(value, dict):
            for key, nested in value.items():
                if isinstance(nested, str) and re.match(r"^[v~^<>=\d]", nested):
                    collect(key)
                else:
                    collect(nested)

    for key, value in stack.items():
        if key.casefold() in DEPENDENCY_KEYS:
            if (
                key.casefold() in {"dependencies", "libraries", "packages"}
                and isinstance(value, dict)
            ):
                for dependency in value:
                    collect(dependency)
            else:
                collect(value)
    return set(dependencies)


def _dependency_name(value: str) -> str:
    candidate = re.split(r"[<>=!~^\s]", value.strip(), maxsplit=1)[0]
    if candidate.startswith("@"):
        separator = candidate.find("@", 1)
        if separator != -1:
            candidate = candidate[:separator]
    elif "@" in candidate:
        candidate = candidate.split("@", 1)[0]
    return candidate.casefold().replace("_", "-")


def _inspect_dependencies(
    project: Project,
    files: dict[str, str],
) -> list[ReviewFinding]:
    findings = []
    expected_frontend = _declared_dependencies(project.frontend_stack)
    package_path = "frontend/package.json"
    if package_path in files:
        try:
            package = json.loads(files[package_path])
            if not isinstance(package, dict):
                raise ValueError("package manifest must be an object")
            dependency_fields = []
            for field in ("dependencies", "devDependencies"):
                values = package.get(field, {})
                if not isinstance(values, dict) or not all(
                    isinstance(name, str)
                    and name.strip()
                    and isinstance(version, str)
                    and version.strip()
                    for name, version in values.items()
                ):
                    raise ValueError(
                        f"package manifest {field} must be an object"
                    )
                dependency_fields.append(values)
            actual_frontend = {
                name.casefold()
                for values in dependency_fields
                for name in values
            }
        except (TypeError, ValueError, json.JSONDecodeError):
            findings.append(
                _finding(
                    "REV-FRONTEND-MANIFEST-INVALID",
                    "high",
                    "frontend",
                    package_path,
                    "implementation.frontend",
                    "Frontend package.json is not valid strict JSON",
                    "Replace package.json with a valid dependency manifest",
                )
            )
        else:
            if actual_frontend != expected_frontend:
                findings.append(
                    _finding(
                        "REV-FRONTEND-DEPENDENCIES",
                        "high",
                        "frontend",
                        package_path,
                        "project.frontend_stack",
                        "Frontend dependencies do not match Project declarations",
                        "Declare exactly the frontend packages authorized by Project",
                    )
                )
    elif expected_frontend:
        findings.append(
            _finding(
                "REV-FRONTEND-MANIFEST-MISSING",
                "high",
                "frontend",
                None,
                "project.frontend_stack",
                "Declared frontend dependencies have no package.json manifest",
                "Add a package.json containing exactly the authorized packages",
            )
        )

    expected_backend = _declared_dependencies(project.backend_stack)
    requirements_path = "backend/requirements.txt"
    if requirements_path in files:
        actual_backend = {
            _dependency_name(line)
            for line in files[requirements_path].splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        if actual_backend != expected_backend:
            findings.append(
                _finding(
                    "REV-BACKEND-DEPENDENCIES",
                    "high",
                    "backend",
                    requirements_path,
                    "project.backend_stack",
                    "Backend dependencies do not match Project declarations",
                    "Declare exactly the backend packages authorized by Project",
                )
            )
    elif expected_backend:
        findings.append(
            _finding(
                "REV-BACKEND-MANIFEST-MISSING",
                "high",
                "backend",
                None,
                "project.backend_stack",
                "Declared backend dependencies have no requirements.txt manifest",
                "Add requirements.txt with exactly the authorized packages",
            )
        )
    return findings


def _inspect_python_references(
    files: dict[str, str],
    project: Project,
) -> list[ReviewFinding]:
    python_files = {
        path: content for path, content in files.items() if path.endswith(".py")
    }
    findings = []
    authorized = _declared_dependencies(project.backend_stack)
    local_roots = {
        part
        for path in python_files
        for part in path.split("/")[:-1]
    } | {
        posixpath.splitext(posixpath.basename(path))[0]
        for path in python_files
    }
    normalized_roots = {item.replace("_", "-") for item in local_roots}
    for path, content in python_files.items():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            findings.append(
                _finding(
                    f"REV-PYTHON-SYNTAX-{_slug(path)}",
                    "critical",
                    "backend",
                    path,
                    "implementation.backend",
                    "Python source does not parse",
                    "Correct the syntax before review",
                )
            )
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports = [(alias.name, None, 0) for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                imports = [
                    (node.module or "", alias.name, node.level)
                    for alias in node.names
                ]
            else:
                continue
            for module, symbol, level in imports:
                top = (
                    module.split(".", 1)[0].replace("_", "-")
                    if module
                    else ""
                )
                if level:
                    target = _relative_python_target(
                        path, module, level, python_files
                    )
                    if target is None:
                        findings.append(
                            _broken_import_finding(path, module or ".", "backend")
                        )
                    elif (
                        symbol
                        and symbol != "*"
                        and not _python_exports(python_files[target], symbol)
                    ):
                        findings.append(
                            _broken_symbol_finding(
                                path, symbol, target, "backend"
                            )
                        )
                elif top in sys.stdlib_module_names or top in authorized:
                    continue
                elif top in normalized_roots:
                    target = _absolute_python_target(module, python_files)
                    if target is None:
                        findings.append(
                            _broken_import_finding(path, module, "backend")
                        )
                    elif (
                        symbol
                        and symbol != "*"
                        and not _python_exports(python_files[target], symbol)
                    ):
                        findings.append(
                            _broken_symbol_finding(
                                path, symbol, target, "backend"
                            )
                        )
                else:
                    findings.append(
                        _finding(
                            f"REV-BACKEND-UNAUTHORIZED-{_slug(path)}-{_slug(top)}",
                            "high",
                            "backend",
                            path,
                            "project.backend_stack",
                            f"Python imports unauthorized dependency {top}",
                            "Remove the import or declare the package in Project",
                        )
                    )
        for undefined in _undefined_python_names(content, path):
            findings.append(
                _finding(
                    f"REV-BROKEN-REFERENCE-{_slug(path)}-{_slug(undefined)}",
                    "critical",
                    "backend",
                    path,
                    "implementation.backend",
                    f"Python references undefined global {undefined}",
                    "Define or import the referenced symbol",
                )
            )
    return _deduplicate_findings(findings)


def _relative_python_target(
    path: str,
    module: str,
    level: int,
    files: dict[str, str],
) -> str | None:
    base = posixpath.dirname(path)
    for _ in range(max(0, level - 1)):
        base = posixpath.dirname(base)
    candidate = (
        posixpath.join(base, *module.split(".")) if module else base
    )
    possibilities = (f"{candidate}.py", f"{candidate}/__init__.py")
    return next((item for item in possibilities if item in files), None)


def _absolute_python_target(module: str, files: dict[str, str]) -> str | None:
    relative = module.replace(".", "/")
    base = [f"{relative}.py", f"{relative}/__init__.py"]
    possibilities = base + [
        f"{root}/{item}" for root in ("backend", "backend/src") for item in base
    ]
    return next((item for item in possibilities if item in files), None)


def _python_exports(content: str, symbol: str) -> bool:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return False
    return any(
        (
            isinstance(
                node,
                (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
            )
            and node.name == symbol
        )
        or (
            isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(name == symbol for name in _assigned_names(node))
        )
        for node in tree.body
    )


def _assigned_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return [target.id for target in targets if isinstance(target, ast.Name)]


def _undefined_python_names(content: str, path: str) -> set[str]:
    try:
        table = symtable.symtable(content, path, "exec")
    except SyntaxError:
        return set()
    module_definitions = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned()
        or symbol.is_imported()
        or symbol.is_namespace()
    }
    allowed = module_definitions | set(dir(builtins)) | {"__name__"}
    return {
        symbol.get_name()
        for scope in _walk_symbol_tables(table)
        for symbol in scope.get_symbols()
        if symbol.is_referenced()
        and symbol.is_global()
        and symbol.get_name() not in allowed
    }


def _walk_symbol_tables(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from _walk_symbol_tables(child)


def _raises_not_implemented(node: ast.stmt) -> bool:
    if not isinstance(node, ast.Raise):
        return False
    exception = node.exc
    if isinstance(exception, ast.Call):
        exception = exception.func
    return isinstance(exception, ast.Name) and exception.id == "NotImplementedError"


def _inspect_javascript_references(
    files: dict[str, str],
    project: Project,
) -> list[ReviewFinding]:
    source_files = {
        path: content
        for path, content in files.items()
        if path.endswith(JAVASCRIPT_SUFFIXES)
    }
    authorized = _declared_dependencies(project.frontend_stack)
    findings = []
    for path, content in source_files.items():
        for specifier in _javascript_imports(content):
            if specifier.startswith(("./", "../")):
                target = _relative_javascript_target(
                    path, specifier, set(source_files)
                )
                if target is None:
                    findings.append(
                        _broken_import_finding(path, specifier, "frontend")
                    )
                    continue
                for symbol in _javascript_imported_symbols(content, specifier):
                    if not _javascript_exports(source_files[target], symbol):
                        findings.append(
                            _broken_symbol_finding(
                                path, symbol, target, "frontend"
                            )
                        )
            else:
                dependency = _javascript_dependency(specifier)
                if dependency not in authorized:
                    findings.append(
                        _finding(
                            "REV-FRONTEND-UNAUTHORIZED-"
                            f"{_slug(path)}-{_slug(dependency)}",
                            "high",
                            "frontend",
                            path,
                            "project.frontend_stack",
                            f"Frontend imports unauthorized dependency {dependency}",
                            "Remove the import or declare the package in Project",
                        )
                    )
    return _deduplicate_findings(findings)


def _javascript_imports(content: str) -> set[str]:
    patterns = (
        r"\b(?:import|export)\s+(?:[^;]*?\s+from\s+)?[\"']([^\"']+)[\"']",
        r"\brequire\s*\(\s*[\"']([^\"']+)[\"']\s*\)",
        r"\bimport\s*\(\s*[\"']([^\"']+)[\"']\s*\)",
    )
    return {
        match.group(1)
        for pattern in patterns
        for match in re.finditer(pattern, content)
    }


def _relative_javascript_target(
    path: str,
    specifier: str,
    paths: set[str],
) -> str | None:
    candidate = posixpath.normpath(
        posixpath.join(posixpath.dirname(path), specifier)
    )
    possibilities = {candidate}
    possibilities.update(candidate + suffix for suffix in JAVASCRIPT_SUFFIXES)
    possibilities.update(
        f"{candidate}/index{suffix}" for suffix in JAVASCRIPT_SUFFIXES
    )
    return next(iter(sorted(possibilities.intersection(paths))), None)


def _javascript_imported_symbols(content: str, specifier: str) -> set[str]:
    symbols = set()
    pattern = (
        r"\bimport\s+(.*?)\s+from\s+[\"']"
        + re.escape(specifier)
        + r"[\"']"
    )
    for match in re.finditer(pattern, content, re.DOTALL):
        named = re.search(r"\{([^}]*)\}", match.group(1), re.DOTALL)
        if named:
            symbols.update(
                item.split(" as ")[0].removeprefix("type ").strip()
                for item in named.group(1).split(",")
                if item.strip()
            )
    return symbols


def _javascript_exports(content: str, symbol: str) -> bool:
    return bool(
        re.search(
            r"\bexport\s+(?:declare\s+)?(?:async\s+)?"
            r"(?:const|let|var|function|class|interface|type|enum)\s+"
            + re.escape(symbol)
            + r"\b",
            content,
        )
        or re.search(
            r"\bexport\s*\{[^}]*\b" + re.escape(symbol) + r"\b",
            content,
            re.DOTALL,
        )
    )


def _javascript_dependency(specifier: str) -> str:
    parts = specifier.split("/")
    if specifier.startswith("@"):
        return "/".join(parts[:2]).casefold()
    return parts[0].casefold()


def _broken_import_finding(
    path: str,
    specifier: str,
    scope: str,
) -> ReviewFinding:
    return _finding(
        f"REV-BROKEN-IMPORT-{_slug(path)}-{_slug(specifier)}",
        "critical",
        scope,
        path,
        f"implementation.{scope}",
        f"Import {specifier} does not resolve to a workspace file",
        "Correct the import path or add the referenced module",
    )


def _broken_symbol_finding(
    path: str,
    symbol: str,
    target: str,
    scope: str,
) -> ReviewFinding:
    return _finding(
        f"REV-BROKEN-SYMBOL-{_slug(path)}-{_slug(symbol)}",
        "critical",
        scope,
        path,
        f"implementation.{scope}",
        f"Imported symbol {symbol} is not exported by {target}",
        "Export the symbol or correct the importing statement",
    )


def _inspect_contracts(
    architecture: ArchitectureArtifact,
    files: dict[str, str],
) -> list[ReviewFinding]:
    modules = {module["name"]: module for module in architecture.modules}
    module_sources = {
        name: "\n".join(
            files[path]
            for path in _module_files(module, files)
            if path.endswith(SOURCE_SUFFIXES)
        ).casefold()
        for name, module in modules.items()
    }
    findings = []
    for interface in architecture.interfaces:
        for operation in interface["operations"]:
            operation_name = operation["name"]
            connected = [interface["provider"], *interface["consumers"]]
            for module_name in connected:
                if _identifier_present(
                    module_sources.get(module_name, ""), operation_name
                ):
                    continue
                findings.append(
                    _finding(
                        "REV-INTERFACE-"
                        f"{_slug(interface['name'])}-"
                        f"{_slug(operation_name)}-{_slug(module_name)}",
                        "high",
                        "integration",
                        _first_module_source(
                            modules.get(module_name), files
                        ),
                        "architecture.interfaces",
                        f"Module {module_name} does not implement interface "
                        f"operation {operation_name}",
                        f"Implement {operation_name} according to interface "
                        f"{interface['name']}",
                    )
                )
    for api in architecture.apis:
        for operation in api["operations"]:
            connected = [api["provider"], *api["consumers"]]
            for module_name in connected:
                source = module_sources.get(module_name, "")
                contains_path = _source_contains_path(
                    source, operation["path"]
                )
                valid = (
                    contains_path
                    and (
                        _source_contains_method(
                            source, operation["method"]
                        )
                        or (
                            operation["method"].casefold() == "get"
                            and _source_contains_default_get_fetch(
                                source, operation["path"]
                            )
                        )
                    )
                )
                if valid:
                    continue
                findings.append(
                    _finding(
                        f"REV-API-{_slug(api['name'])}-"
                        f"{_slug(operation['name'])}-{_slug(module_name)}",
                        "critical",
                        "integration",
                        _first_module_source(
                            modules.get(module_name), files
                        ),
                        "integration.backend_frontend",
                        f"Module {module_name} is incompatible with "
                        f"{operation['method']} {operation['path']}",
                        f"Implement the exact {api['name']} operation on {module_name}",
                    )
                )
    return findings


def _inspect_persistence(
    architecture: ArchitectureArtifact,
    files: dict[str, str],
) -> list[ReviewFinding]:
    modules = {module["name"]: module for module in architecture.modules}
    findings = []
    for store in architecture.persistence.get("stores", []):
        owner = modules.get(store["owned_by"])
        source = "\n".join(
            files[path]
            for path in _module_files(owner, files)
            if path.endswith(SOURCE_SUFFIXES)
        ).casefold() if owner is not None else ""
        for model in store["data_models"]:
            if _identifier_present(source, model["name"]):
                continue
            findings.append(
                _finding(
                    f"REV-PERSISTENCE-{_slug(store['name'])}-{_slug(model['name'])}",
                    "high",
                    "architecture",
                    _first_module_source(owner, files),
                    "architecture.persistence",
                    f"Persistence model {model['name']} is absent from "
                    f"owner {store['owned_by']}",
                    f"Implement {model['name']} for store {store['name']}",
                )
            )
    return findings


def _first_module_source(
    module: dict[str, Any] | None,
    files: dict[str, str],
) -> str | None:
    if module is None:
        return None
    return next(
        (
            path
            for path in _module_files(module, files)
            if path.endswith(SOURCE_SUFFIXES)
        ),
        None,
    )


def _identifier_present(source: str, identifier: str) -> bool:
    normalized = identifier.casefold()
    compact = normalized.replace("_", "")
    return bool(
        re.search(
            r"\b(?:"
            + re.escape(normalized)
            + "|"
            + re.escape(compact)
            + r")\b",
            source,
        )
    )


def _source_contains_path(source: str, declared_path: str) -> bool:
    path = declared_path.casefold()
    if path in source:
        return True
    parts = []
    position = 0
    for match in re.finditer(r"\{([A-Za-z_$][\w$]*)\}", path):
        parts.append(re.escape(path[position : match.start()]))
        parameter = match.group(1)
        compact = parameter.replace("_", "")
        parameter_pattern = (
            f"(?:{re.escape(parameter)}|{re.escape(compact)})"
        )
        parts.append(
            r"(?:\{"
            + re.escape(parameter)
            + r"\}|\$\{[^}\n]*\b"
            + parameter_pattern
            + r"\b[^}\n]*\}|[\"'`]\s*\+\s*[^;\n]*\b"
            + parameter_pattern
            + r"\b)"
        )
        position = match.end()
    if not parts:
        return False
    parts.append(re.escape(path[position:]))
    return re.search("".join(parts), source) is not None


def _source_contains_method(source: str, method: str) -> bool:
    normalized = re.escape(method.casefold())
    return bool(
        re.search(
            rf"(?:[\"']{normalized}[\"']|\.{normalized}\s*\(|"
            rf"\bmethod\s*[:=]\s*[\"']{normalized}[\"'])",
            source,
        )
    )


def _source_contains_default_get_fetch(
    source: str,
    declared_path: str,
) -> bool:
    """Recognize a browser fetch whose omitted method is HTTP GET."""
    path = re.escape(declared_path.casefold())
    return bool(
        re.search(
            rf"\bfetch\s*\(\s*([\"'`]){path}\1\s*\)",
            source,
        )
    )


def _deduplicate_findings(
    findings: list[ReviewFinding],
) -> list[ReviewFinding]:
    return list({finding.id: finding for finding in findings}.values())


def _slug(value: str) -> str:
    return (
        re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
        or "unknown"
    )
