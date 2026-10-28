import json
import posixpath
import re
from typing import Any

from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.messaging import TaskHandler
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ProjectCodeWorkspace
from core.models.architecture import ArchitectureArtifact, ArchitectureStatus
from core.models.frontend_generation import (
    FrontendGenerationArtifact,
    FrontendGenerationStatus,
    FrontendGenerationValidationError,
)
from core.models.project import Project
from core.models.task import Task, TaskStatus
from core.models.task_result import TaskExecutionResult
from core.models.workspace import (
    WorkspaceFile,
    is_editable_workspace_path,
)


class FrontendProjectNotFoundError(LookupError):
    """Raised when a frontend task references an unknown project."""


ARCHITECTURE_AUTHORITATIVE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
    "technical_constraints",
)

FRONTEND_AUTHORITATIVE_FIELDS = (
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
    "technical_constraints",
)

REQUIRED_FRONTEND_FIELDS = (
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
)

FRONTEND_DEPENDENCY_KEYS = frozenset(
    {
        "dependencies",
        "framework",
        "frameworks",
        "libraries",
        "library",
        "package",
        "packages",
    }
)

SOURCE_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx")
PLACEHOLDER_MARKERS = (
    "todo",
    "fixme",
    "placeholder",
    "example usage",
    "replace with actual",
)
MAX_GENERATION_ATTEMPTS = 4
TYPESCRIPT_BUILD_PACKAGES = frozenset(
    {
        "@angular/cli",
        "esbuild",
        "next",
        "parcel",
        "react-scripts",
        "rollup",
        "typescript",
        "vite",
        "webpack",
    }
)


class FrontendAgent(TaskHandler):
    def __init__(
        self,
        task_status_writer: TaskStatusWriter,
        task_result_writer: TaskResultWriter,
        project_reader: ProjectReader,
        workspace: ProjectCodeWorkspace,
        engine: AgentEngine,
        context_provider: ContextProvider,
    ):
        self.task_status_writer = task_status_writer
        self.task_result_writer = task_result_writer
        self.project_reader = project_reader
        self.workspace = workspace
        self.engine = engine
        self.context_provider = context_provider

    def handle(self, task: Task) -> None:
        try:
            self._update_status(task, TaskStatus.IN_PROGRESS)
            project = self.project_reader.find_by_id(task.project_id)
            if project is None:
                raise FrontendProjectNotFoundError(
                    f"Project not found: {task.project_id}"
                )

            architecture = self.workspace.read_architecture(task.project_id)
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
                for field_name in REQUIRED_FRONTEND_FIELDS
                if not getattr(project, field_name)
            ]
            if missing_decisions:
                self._persist_needs_input(
                    task,
                    project,
                    missing_decisions,
                )
                return

            if (
                str(project.frontend_stack.get("language", "")).casefold()
                == "typescript"
                and not TYPESCRIPT_BUILD_PACKAGES.intersection(
                    self._declared_dependencies(project)
                )
            ):
                self._persist_needs_input(
                    task,
                    project,
                    ["frontend_build_tool"],
                )
                return

            existing_files = [
                workspace_file
                for workspace_file in self.workspace.read_files(
                    task.project_id
                )
                if workspace_file.relative_path.startswith("frontend/")
                and is_editable_workspace_path(
                    workspace_file.relative_path
                )
            ]
            validation_feedback = None
            for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
                engine_result = self.process(
                    task,
                    project,
                    architecture,
                    functional_contract,
                    existing_files,
                    validation_feedback,
                )
                try:
                    generation = FrontendGenerationArtifact.from_json(
                        engine_result.output
                    )
                    self._validate_frontend_authority(project, generation)
                    if generation.status is FrontendGenerationStatus.COMPLETE:
                        self._validate_complete_generation(
                            project,
                            architecture,
                            generation,
                            existing_files,
                        )
                    break
                except FrontendGenerationValidationError as error:
                    if attempt == MAX_GENERATION_ATTEMPTS:
                        raise
                    validation_feedback = self._validation_feedback(
                        project,
                        attempt,
                        error,
                    )
            self._persist_result(task, engine_result, generation)

            if generation.status is FrontendGenerationStatus.NEEDS_INPUT:
                self._update_status(task, TaskStatus.NEEDS_INPUT)
                return

            self.workspace.write_files(task.project_id, generation.files)
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
        functional_contract: dict[str, Any] | None,
        existing_files: list[WorkspaceFile],
        validation_feedback: dict[str, Any] | None = None,
    ) -> EngineResult:
        context = {
            **self.context_provider.build(task, project),
            "output_contract": self._output_contract(project, architecture),
            "frontend_implementation": self._implementation_context(
                project,
                architecture,
                functional_contract,
                existing_files,
            ),
        }
        if validation_feedback is not None:
            context["frontend_validation_feedback"] = validation_feedback
        return self.engine.run(
            task=task,
            project=project,
            context=context,
        )

    def _read_functional_contract(
        self,
        task: Task,
        project: Project,
    ) -> dict[str, Any] | None:
        if task.spec_path is None:
            return None
        project_spec = self.workspace.read_project_specification(
            task.project_id
        )
        specification = self.workspace.read_specification(
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
        generation = FrontendGenerationArtifact(
            version=1,
            status=FrontendGenerationStatus.NEEDS_INPUT,
            missing_decisions=missing_decisions,
            frontend_stack=dict(project.frontend_stack),
            frontend_architecture=dict(project.frontend_architecture),
            infrastructure=dict(project.infrastructure),
            technical_constraints=list(project.technical_constraints),
            files=[],
            implementation={
                "modules": [],
                "interfaces": [],
                "apis": [],
                "dependencies": [],
                "file_modules": {},
            },
            summary="Frontend generation requires architecture decisions",
        )
        result = EngineResult(
            output=generation.to_json(),
            metadata={"missing_decisions": missing_decisions},
            provider="agentforge",
            model_alias=None,
        )
        self._persist_result(task, result, generation)
        self._update_status(task, TaskStatus.NEEDS_INPUT)

    def _persist_result(
        self,
        task: Task,
        engine_result: EngineResult,
        generation: FrontendGenerationArtifact,
    ) -> None:
        self.task_result_writer.write(
            TaskExecutionResult(
                task_id=task.id,
                project_id=task.project_id,
                agent=str(task.agent),
                provider=engine_result.provider,
                model_alias=engine_result.model_alias,
                output=generation.to_json(),
                metadata=dict(engine_result.metadata),
            )
        )

    @staticmethod
    def _authoritative_frontend(project: Project) -> dict[str, Any]:
        return {
            field_name: getattr(project, field_name)
            for field_name in FRONTEND_AUTHORITATIVE_FIELDS
        }

    @classmethod
    def _output_contract(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
    ) -> dict[str, Any]:
        contract = FrontendGenerationArtifact.output_contract()
        authoritative = cls._authoritative_frontend(project)
        properties = contract["properties"]
        for field_name, value in authoritative.items():
            field_contract = dict(properties[field_name])
            field_contract["const"] = value
            properties[field_name] = field_contract

        requirements = cls._frontend_requirements(architecture)
        implementation = properties["implementation"]["properties"]
        for field_name, value in requirements.items():
            field_contract = dict(implementation[field_name])
            field_contract["const"] = value
            implementation[field_name] = field_contract
        dependencies = cls._declared_dependencies(project)
        dependency_contract = dict(implementation["dependencies"])
        dependency_contract["const"] = dependencies
        implementation["dependencies"] = dependency_contract
        if len(requirements["modules"]) == 1:
            implementation["file_modules"] = {
                "type": "object",
                "const": {},
            }
        else:
            implementation["file_modules"]["additionalProperties"] = {
                "type": "string",
                "enum": requirements["modules"],
            }
        file_properties = properties["files"]["items"]["properties"]
        file_properties["content"] = {
            "type": "string",
            "minLength": 1,
        }

        contract["instruction"] = (
            f"{contract['instruction']} The Project and validated "
            "architecture artifact are authoritative. Copy all technical "
            "fields exactly. Use only their languages, frameworks, primary "
            "libraries, infrastructure and architecture rules. Implement "
            "those rules in real file structure and responsibilities, not "
            "only in comments. Cover exactly the declared frontend modules "
            "and the interfaces and APIs they consume. The only permitted "
            "package/import names are "
            f"{json.dumps(dependencies)}. Include a valid package.json whose "
            "dependencies and devDependencies together match that exact "
            "list and a real build script. Every relative source import must "
            "resolve to a generated or existing frontend file. Do not emit "
            "TODO, FIXME, placeholders, missing symbols, broken references, "
            "or incomplete source. When there is one frontend module, return "
            "an empty file_modules object; AgentForge derives its mapping. "
            "If a required decision is absent, return "
            "needs_input with no files."
        )
        build_guidance = cls._build_guidance(project)
        if build_guidance is not None:
            contract["instruction"] += (
                " For dependency-free JavaScript, use exactly the supplied "
                "package.json build manifest and frontend/app.js entry path; "
                "do not omit scripts.build."
            )
        return contract

    @classmethod
    def _implementation_context(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
        functional_contract: dict[str, Any] | None,
        existing_files: list[WorkspaceFile],
    ) -> dict[str, Any]:
        mandatory_rules = [
            {
                "source": "frontend_stack",
                "requirement": json.dumps(
                    project.frontend_stack,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
            {
                "source": "frontend_architecture",
                "requirement": json.dumps(
                    project.frontend_architecture,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
            {
                "source": "infrastructure",
                "requirement": json.dumps(
                    project.infrastructure,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        ]
        mandatory_rules.extend(
            {
                "source": f"technical_constraints[{index}]",
                "requirement": constraint,
            }
            for index, constraint in enumerate(
                project.technical_constraints
            )
        )
        return {
            "mandatory_rules": mandatory_rules,
            "architecture_artifact": architecture.to_dict(),
            "functional_contract": functional_contract,
            "frontend_requirements": cls._frontend_requirements(architecture),
            "authorized_dependencies": cls._declared_dependencies(project),
            "build_contract": cls._build_guidance(project),
            "existing_files": [
                {
                    "path": workspace_file.relative_path,
                    "content": workspace_file.content,
                }
                for workspace_file in existing_files
            ],
            "generation_rules": [
                (
                    "Use only the framework and packages explicitly declared "
                    "by frontend_stack; transitive packages are forbidden."
                ),
                (
                    "Implement every consumed interface operation and API "
                    "method/path exactly as architecture.json declares it."
                ),
                (
                    "Provide package.json with a deterministic build script "
                    "that compiles or validates the generated application."
                ),
                (
                    "All imports and referenced symbols must exist; do not "
                    "emit placeholders or illustrative pseudo-code."
                ),
            ],
        }

    @classmethod
    def _build_guidance(cls, project: Project) -> dict[str, Any] | None:
        language = str(project.frontend_stack.get("language", "")).casefold()
        if language not in {"javascript", "ecmascript"} or (
            cls._declared_dependencies(project)
        ):
            return None
        package = {
            "type": "module",
            "scripts": {"build": "node --check app.js"},
            "dependencies": {},
            "devDependencies": {},
        }
        return {
            "package_path": "frontend/package.json",
            "source_entry_path": "frontend/app.js",
            "exact_package_json": json.dumps(
                package,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "required_build_command": "node --check app.js",
        }

    @classmethod
    def _validation_feedback(
        cls,
        project: Project,
        attempt: int,
        error: FrontendGenerationValidationError,
    ) -> dict[str, Any]:
        build_contract = cls._build_guidance(project)
        return {
            "attempt": attempt,
            "validation_error": str(error),
            "authorized_dependencies": cls._declared_dependencies(project),
            "required_build_contract": build_contract,
            "instruction": (
                "Regenerate the full corrected artifact rather than copying "
                "the invalid files. Preserve Project and architecture.json "
                "exactly. If required_build_contract is present, copy its "
                "package path, source entry path and exact package JSON. Do "
                "not add packages, protocols, imports, symbols or files "
                "outside the contract."
            ),
        }

    @staticmethod
    def _validate_architecture_authority(
        project: Project,
        architecture: ArchitectureArtifact,
    ) -> None:
        mismatches = [
            field_name
            for field_name in ARCHITECTURE_AUTHORITATIVE_FIELDS
            if getattr(architecture, field_name) != getattr(
                project,
                field_name,
            )
        ]
        if mismatches:
            raise FrontendGenerationValidationError(
                "Architecture artifact contradicts Project fields: "
                + ", ".join(mismatches)
            )

    @classmethod
    def _validate_frontend_authority(
        cls,
        project: Project,
        generation: FrontendGenerationArtifact,
    ) -> None:
        mismatches = [
            field_name
            for field_name, expected in cls._authoritative_frontend(
                project
            ).items()
            if getattr(generation, field_name) != expected
        ]
        if mismatches:
            raise FrontendGenerationValidationError(
                "Frontend output contradicts authoritative Project fields: "
                + ", ".join(mismatches)
            )

    @staticmethod
    def _frontend_requirements(
        architecture: ArchitectureArtifact,
    ) -> dict[str, list[str]]:
        modules_by_name = {
            module["name"]: module
            for module in architecture.modules
        }
        backend_modules = {
            contract["provider"]
            for contract in [
                *architecture.interfaces,
                *architecture.apis,
            ]
        }
        backend_modules.update(
            store["owned_by"]
            for store in architecture.persistence.get("stores", [])
        )
        pending = list(backend_modules)
        while pending:
            module_name = pending.pop()
            for dependency in modules_by_name[module_name]["dependencies"]:
                if dependency not in backend_modules:
                    backend_modules.add(dependency)
                    pending.append(dependency)
        modules = sorted(set(modules_by_name).difference(backend_modules))
        return {
            "modules": modules,
            "interfaces": sorted(
                interface["name"]
                for interface in architecture.interfaces
                if set(interface["consumers"]).intersection(modules)
            ),
            "apis": sorted(
                api["name"]
                for api in architecture.apis
                if set(api["consumers"]).intersection(modules)
            ),
        }

    @staticmethod
    def _declared_dependencies(project: Project) -> list[str]:
        dependencies = []

        def collect(value: Any) -> None:
            if isinstance(value, str) and value.strip():
                dependency = _dependency_name(value)
                if dependency:
                    dependencies.append(dependency)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)
            elif isinstance(value, dict):
                for nested in value.values():
                    collect(nested)

        for key, value in project.frontend_stack.items():
            if key.casefold() in FRONTEND_DEPENDENCY_KEYS:
                if (
                    key.casefold()
                    in {"dependencies", "libraries", "packages"}
                    and isinstance(value, dict)
                ):
                    for dependency_name in value:
                        collect(dependency_name)
                else:
                    collect(value)
        return list(dict.fromkeys(dependencies))

    @classmethod
    def _validate_complete_generation(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
        generation: FrontendGenerationArtifact,
        existing_files: list[WorkspaceFile],
    ) -> None:
        requirements = cls._frontend_requirements(architecture)
        for field_name, expected in requirements.items():
            if sorted(generation.implementation[field_name]) != expected:
                raise FrontendGenerationValidationError(
                    "Frontend implementation does not cover architecture "
                    f"{field_name}"
                )

        authorized = set(cls._declared_dependencies(project))
        declared = set(generation.implementation["dependencies"])
        if declared != authorized:
            raise FrontendGenerationValidationError(
                "Frontend dependency manifest does not match Project"
            )

        generated_paths = {
            workspace_file.relative_path for workspace_file in generation.files
        }
        combined_files = {
            workspace_file.relative_path: workspace_file
            for workspace_file in existing_files
        }
        combined_files.update(
            {
                workspace_file.relative_path: workspace_file
                for workspace_file in generation.files
            }
        )
        _validate_frontend_workspace(
            combined_files,
            generated_paths,
            authorized,
        )

        source = "\n".join(
            workspace_file.content for workspace_file in generation.files
        )
        normalized_source = source.casefold()
        language = str(
            project.frontend_stack.get("language", "")
        ).casefold()
        generated_source_paths = {
            path
            for path in generated_paths
            if path.endswith(SOURCE_SUFFIXES)
        }
        if language == "typescript" and not any(
            path.endswith((".ts", ".tsx"))
            for path in generated_source_paths
        ):
            raise FrontendGenerationValidationError(
                "TypeScript frontend must generate TypeScript source"
            )
        if language in {"javascript", "ecmascript"} and any(
            path.endswith((".ts", ".tsx"))
            for path in generated_source_paths
        ):
            raise FrontendGenerationValidationError(
                "JavaScript frontend cannot generate TypeScript source"
            )
        framework_dependencies = set()
        for key, value in project.frontend_stack.items():
            if key.casefold() not in {"framework", "frameworks"}:
                continue
            values = value if isinstance(value, list) else [value]
            framework_dependencies.update(
                _dependency_name(item)
                for item in values
                if isinstance(item, str) and item.strip()
            )
        imported_dependencies = {
            _import_dependency(specifier)
            for specifier in _source_imports(source)
            if not specifier.startswith(("./", "../"))
        }
        unused_frameworks = framework_dependencies.difference(
            imported_dependencies
        )
        if unused_frameworks == {"react"} and re.search(
            r"<[A-Za-z][^>]*>",
            source,
        ):
            unused_frameworks.clear()
        if unused_frameworks:
            raise FrontendGenerationValidationError(
                "Frontend source does not use declared framework: "
                + ", ".join(sorted(unused_frameworks))
            )
        for interface in architecture.interfaces:
            if interface["name"] not in requirements["interfaces"]:
                continue
            for operation in interface["operations"]:
                if operation["name"].casefold() not in normalized_source:
                    raise FrontendGenerationValidationError(
                        "Frontend output does not consume architecture "
                        f"interface operation: {operation['name']}"
                    )
        for api in architecture.apis:
            if api["name"] not in requirements["apis"]:
                continue
            for operation in api["operations"]:
                method = operation["method"].casefold()
                if (
                    operation["name"].casefold() not in normalized_source
                    or method not in normalized_source
                    or not _source_contains_api_path(
                        normalized_source,
                        operation["path"],
                    )
                ):
                    raise FrontendGenerationValidationError(
                        "Frontend output does not consume architecture API: "
                        f"{operation['method']} {operation['path']}"
                    )

    def _update_status(self, task: Task, status: TaskStatus) -> None:
        self.task_status_writer.update_status(task.id, status)
        task.status = status


def _dependency_name(value: str) -> str:
    candidate = re.split(r"[<>=!~\s]", value.strip(), maxsplit=1)[0]
    if candidate.startswith("@"):
        version_separator = candidate.find("@", 1)
        if version_separator != -1:
            candidate = candidate[:version_separator]
    elif "@" in candidate:
        candidate = candidate.split("@", 1)[0]
    return candidate.casefold()


def _source_contains_api_path(source: str, declared_path: str) -> bool:
    normalized_path = declared_path.casefold()
    if normalized_path in source:
        return True

    pattern_parts = []
    position = 0
    for match in re.finditer(r"\{([A-Za-z_$][\w$]*)\}", normalized_path):
        pattern_parts.append(re.escape(normalized_path[position:match.start()]))
        parameter = match.group(1)
        compact_parameter = parameter.replace("_", "")
        parameter_pattern = (
            re.escape(parameter)
            if compact_parameter == parameter
            else f"(?:{re.escape(parameter)}|{re.escape(compact_parameter)})"
        )
        pattern_parts.append(
            r"(?:"
            + re.escape(match.group(0))
            + r"|\$\{[^}\n]*\b"
            + parameter_pattern
            + r"\b[^}\n]*\}"
            + r"|[\"'`]\s*\+\s*"
            + parameter_pattern
            + r"\b)"
        )
        position = match.end()

    if not pattern_parts:
        return False
    pattern_parts.append(re.escape(normalized_path[position:]))
    return re.search("".join(pattern_parts), source) is not None


def _validate_frontend_workspace(
    files: dict[str, WorkspaceFile],
    generated_paths: set[str],
    authorized_dependencies: set[str],
) -> None:
    package_file = files.get("frontend/package.json")
    if package_file is None:
        raise FrontendGenerationValidationError(
            "Frontend output requires frontend/package.json"
        )
    try:
        package = json.loads(
            package_file.content,
            parse_constant=_reject_package_json_constant,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise FrontendGenerationValidationError(
            "Frontend package.json must be strict JSON"
        ) from error
    if not isinstance(package, dict):
        raise FrontendGenerationValidationError(
            "Frontend package.json must be an object"
        )
    scripts = package.get("scripts")
    if (
        not isinstance(scripts, dict)
        or not isinstance(scripts.get("build"), str)
        or not scripts["build"].strip()
    ):
        raise FrontendGenerationValidationError(
            "Frontend package.json requires a non-empty build script"
        )
    build_command = scripts["build"].strip()
    first_executable = build_command.split(maxsplit=1)[0].casefold()
    if first_executable in {":", "echo", "printf", "true"}:
        raise FrontendGenerationValidationError(
            "Frontend package.json build script cannot be a no-op"
        )

    package_dependencies = set()
    for field_name in ("dependencies", "devDependencies"):
        values = package.get(field_name, {})
        if not isinstance(values, dict) or not all(
            isinstance(name, str)
            and name.strip()
            and isinstance(version, str)
            and version.strip()
            for name, version in values.items()
        ):
            raise FrontendGenerationValidationError(
                f"Frontend package.json {field_name} must map package names "
                "to versions"
            )
        overlap = package_dependencies.intersection(
            name.casefold() for name in values
        )
        if overlap:
            raise FrontendGenerationValidationError(
                "Frontend package.json duplicates dependencies: "
                + ", ".join(sorted(overlap))
            )
        package_dependencies.update(name.casefold() for name in values)
    if package_dependencies != authorized_dependencies:
        raise FrontendGenerationValidationError(
            "Frontend package.json dependencies do not match Project; "
            f"expected={sorted(authorized_dependencies)}, "
            f"actual={sorted(package_dependencies)}"
        )

    source_paths = {
        path for path in files if path.endswith(SOURCE_SUFFIXES)
    }
    if not source_paths:
        raise FrontendGenerationValidationError(
            "Frontend output must contain JavaScript or TypeScript source"
        )
    for path in source_paths:
        content = files[path].content
        if path in generated_paths:
            _reject_frontend_placeholders(content, path)
        for specifier in _source_imports(content):
            if specifier.startswith(("./", "../")):
                target = _relative_import_target(
                    path,
                    specifier,
                    source_paths,
                )
                if target is None:
                    raise FrontendGenerationValidationError(
                        "Frontend source imports missing relative reference "
                        f"{specifier} in {path}"
                    )
                _validate_imported_symbols(
                    path,
                    content,
                    specifier,
                    target,
                    files[target].content,
                )
                continue
            dependency = _import_dependency(specifier)
            if dependency not in authorized_dependencies:
                raise FrontendGenerationValidationError(
                    "Frontend source imports undeclared dependency "
                    f"{specifier} in {path}"
                )


def _source_imports(content: str) -> set[str]:
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


def _import_dependency(specifier: str) -> str:
    parts = specifier.split("/")
    if specifier.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2]).casefold()
    return parts[0].casefold()


def _relative_import_target(
    source_path: str,
    specifier: str,
    source_paths: set[str],
) -> str | None:
    candidate = posixpath.normpath(
        posixpath.join(posixpath.dirname(source_path), specifier)
    )
    possibilities = {candidate}
    possibilities.update(candidate + suffix for suffix in SOURCE_SUFFIXES)
    possibilities.update(
        f"{candidate}/index{suffix}" for suffix in SOURCE_SUFFIXES
    )
    matches = sorted(possibilities.intersection(source_paths))
    return matches[0] if matches else None


def _validate_imported_symbols(
    source_path: str,
    content: str,
    specifier: str,
    target_path: str,
    target_content: str,
) -> None:
    export_names = set(
        re.findall(
            r"\bexport\s+(?:declare\s+)?(?:async\s+)?"
            r"(?:const|let|var|function|class|interface|type|enum)\s+"
            r"([A-Za-z_$][\w$]*)",
            target_content,
        )
    )
    for block in re.findall(
        r"\bexport\s*\{([^}]*)\}",
        target_content,
        re.DOTALL,
    ):
        export_names.update(
            part.split(" as ")[-1].strip()
            for part in block.split(",")
            if part.strip()
        )
    has_default = bool(re.search(r"\bexport\s+default\b", target_content))
    statement_pattern = (
        r"\b(?:import|export)\s+(.*?)\s+from\s+[\"']"
        + re.escape(specifier)
        + r"[\"']"
    )
    for match in re.finditer(statement_pattern, content, re.DOTALL):
        clause = match.group(1).strip()
        if clause.startswith("type "):
            clause = clause.removeprefix("type ").strip()
        if clause and not clause.startswith(("{", "*")):
            if not has_default:
                raise FrontendGenerationValidationError(
                    "Frontend source imports missing default export from "
                    f"{target_path} in {source_path}"
                )
        named_match = re.search(r"\{([^}]*)\}", clause, re.DOTALL)
        if named_match is None:
            continue
        imported_names = {
            part.split(" as ")[0].removeprefix("type ").strip()
            for part in named_match.group(1).split(",")
            if part.strip()
        }
        missing = imported_names.difference(export_names)
        if missing:
            raise FrontendGenerationValidationError(
                "Frontend source imports missing symbols from "
                f"{target_path} in {source_path}: "
                + ", ".join(sorted(missing))
            )


def _reject_frontend_placeholders(content: str, path: str) -> None:
    comments = re.findall(r"//[^\n]*|/\*.*?\*/", content, re.DOTALL)
    if any(
        marker in comment.casefold()
        for comment in comments
        for marker in PLACEHOLDER_MARKERS
    ) or re.search(
        r"throw\s+new\s+Error\s*\(\s*[\"'](?:not implemented|todo)",
        content,
        re.IGNORECASE,
    ) or re.search(
        r"[\"'](?:example|sample|dummy)(?:[-_][A-Za-z0-9]+)*[-_]id[\"']",
        content,
        re.IGNORECASE,
    ):
        raise FrontendGenerationValidationError(
            f"Frontend source contains placeholder code: {path}"
        )


def _reject_package_json_constant(value: str) -> None:
    raise ValueError(f"Non-JSON numeric constant: {value}")
