import ast
import builtins
import json
import re
import symtable
import sys
import tokenize
from io import StringIO
from typing import Any

from core.contracts.context import ContextProvider
from core.contracts.engines import AgentEngine, EngineResult
from core.contracts.messaging import TaskHandler
from core.contracts.repositories import ProjectReader, TaskStatusWriter
from core.contracts.results import TaskResultWriter
from core.contracts.workspaces import ProjectCodeWorkspace
from core.models.architecture import ArchitectureArtifact, ArchitectureStatus
from core.models.backend_generation import (
    BackendGenerationArtifact,
    BackendGenerationStatus,
    BackendGenerationValidationError,
)
from core.models.project import Project
from core.models.task import Task, TaskStatus
from core.models.task_result import TaskExecutionResult
from core.models.workspace import WorkspaceFile


class BackendProjectNotFoundError(LookupError):
    """Raised when a backend task references an unknown project."""


ARCHITECTURE_AUTHORITATIVE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "frontend_stack",
    "frontend_architecture",
    "infrastructure",
    "technical_constraints",
)

BACKEND_AUTHORITATIVE_FIELDS = (
    "backend_stack",
    "backend_architecture",
    "infrastructure",
    "technical_constraints",
)

REQUIRED_BACKEND_FIELDS = (
    "backend_stack",
    "backend_architecture",
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

PERSISTENCE_DEPENDENCY_KEYS = frozenset(
    {"orm", "persistence_library", "database_driver"}
)

PLACEHOLDER_MARKERS = ("todo", "fixme", "placeholder")
MAX_GENERATION_ATTEMPTS = 3


class BackendAgent(TaskHandler):
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
        self._update_status(task, TaskStatus.IN_PROGRESS)

        try:
            project = self.project_reader.find_by_id(task.project_id)
            if project is None:
                raise BackendProjectNotFoundError(
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

            missing_decisions = [
                field_name
                for field_name in REQUIRED_BACKEND_FIELDS
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
                architecture.persistence.get("stores")
                and not self._declared_dependencies(
                    project,
                    PERSISTENCE_DEPENDENCY_KEYS,
                )
            ):
                self._persist_needs_input(
                    task,
                    project,
                    ["backend_persistence_library"],
                )
                return

            existing_files = self.workspace.read_files(task.project_id)
            validation_feedback = None
            for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
                engine_result = self.process(
                    task,
                    project,
                    architecture,
                    existing_files,
                    validation_feedback,
                )
                try:
                    generation = BackendGenerationArtifact.from_json(
                        engine_result.output
                    )
                    self._validate_backend_authority(project, generation)
                    if generation.status is BackendGenerationStatus.COMPLETE:
                        self._validate_complete_generation(
                            project,
                            architecture,
                            generation,
                            existing_files,
                        )
                    break
                except BackendGenerationValidationError as error:
                    if attempt == MAX_GENERATION_ATTEMPTS:
                        raise
                    validation_feedback = {
                        "attempt": attempt,
                        "validation_error": str(error),
                        "previous_output": engine_result.output,
                        "instruction": (
                            "Return the full corrected artifact. Preserve "
                            "Project and architecture values exactly and "
                            "change only what is required to satisfy the "
                            "validation error. Undefined names must be "
                            "explicitly imported or defined in the same "
                            "file. For nullable annotations use Python 3.12 "
                            "union syntax instead of Optional. Never replace "
                            "a missing symbol with an undeclared external "
                            "package."
                        ),
                    }
            self._persist_result(task, engine_result, generation)

            if generation.status is BackendGenerationStatus.NEEDS_INPUT:
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
        existing_files: list[WorkspaceFile],
        validation_feedback: dict[str, Any] | None = None,
    ) -> EngineResult:
        context = {
            **self.context_provider.build(task, project),
            "output_contract": self._output_contract(
                project,
                architecture,
            ),
            "backend_implementation": self._implementation_context(
                project,
                architecture,
                existing_files,
            ),
        }
        if validation_feedback is not None:
            context["backend_validation_feedback"] = validation_feedback
        return self.engine.run(
            task=task,
            project=project,
            context=context,
        )

    def _persist_needs_input(
        self,
        task: Task,
        project: Project,
        missing_decisions: list[str],
    ) -> None:
        generation = BackendGenerationArtifact(
            version=1,
            status=BackendGenerationStatus.NEEDS_INPUT,
            missing_decisions=missing_decisions,
            backend_stack=dict(project.backend_stack),
            backend_architecture=dict(project.backend_architecture),
            infrastructure=dict(project.infrastructure),
            technical_constraints=list(project.technical_constraints),
            files=[],
            implementation={
                "modules": [],
                "interfaces": [],
                "apis": [],
                "persistence_stores": [],
                "dependencies": [],
                "file_modules": {},
            },
            summary="Backend generation requires architecture decisions",
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
        generation: BackendGenerationArtifact,
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
    def _authoritative_backend(project: Project) -> dict[str, Any]:
        return {
            field_name: getattr(project, field_name)
            for field_name in BACKEND_AUTHORITATIVE_FIELDS
        }

    @classmethod
    def _output_contract(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
    ) -> dict[str, Any]:
        contract = BackendGenerationArtifact.output_contract()
        authoritative = cls._authoritative_backend(project)
        properties = contract["properties"]
        for field_name, value in authoritative.items():
            field_contract = dict(properties[field_name])
            field_contract["const"] = value
            properties[field_name] = field_contract

        requirements = cls._backend_requirements(architecture)
        module_packages = {
            module: f"backend/{_python_package_name(module)}"
            for module in requirements["modules"]
        }
        implementation = properties["implementation"]["properties"]
        for field_name, value in requirements.items():
            field_contract = dict(implementation[field_name])
            field_contract["const"] = value
            implementation[field_name] = field_contract
        dependency_contract = dict(implementation["dependencies"])
        dependency_contract["const"] = cls._declared_dependencies(project)
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

        file_contract = properties["files"]["items"]["properties"]
        package_pattern = "|".join(
            re.escape(package)
            for package in module_packages.values()
        )
        file_contract["path"] = {
            "type": "string",
            "pattern": rf"^(?:{package_pattern})/.+$",
        }
        file_contract["content"] = {
            "type": "string",
            "minLength": 1,
        }

        contract["instruction"] = (
            f"{contract['instruction']} The Project and validated "
            "architecture artifact are authoritative. Copy all technical "
            "fields exactly. Use only their languages, frameworks, databases, "
            "infrastructure and architecture rules. Implement those rules in "
            "real file structure, responsibilities and dependency direction, "
            "not only in comments. Every file must belong to an implemented "
            "architecture module, and the implementation manifest must cover "
            "the exact backend modules, interfaces, APIs and persistence "
            "stores. List only imported external packages in dependencies. "
            "The complete set of permitted external import roots is "
            f"{json.dumps(cls._declared_dependencies(project))}; do not "
            "import transitive or implicit packages outside that list. "
            "Every internal import must be the exact dotted form of another "
            "generated or existing Python file path; never turn logical "
            "architecture module names into undeclared package aliases. "
            "Use this exact architecture-module to Python-package mapping: "
            f"{json.dumps(module_packages, sort_keys=True)}. "
            "When there is exactly one backend module, return an empty "
            "file_modules object; AgentForge derives that unambiguous map. "
            "Do not emit pass, ellipsis, TODO, FIXME, placeholders, missing "
            "imports, undefined references or undeclared packages. If a "
            "required decision is absent, return needs_input with no files "
            "and empty implementation coverage."
        )
        return contract

    @classmethod
    def _implementation_context(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
        existing_files: list[WorkspaceFile],
    ) -> dict[str, Any]:
        mandatory_rules = [
            {
                "source": "backend_stack",
                "requirement": json.dumps(
                    project.backend_stack,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
            {
                "source": "backend_architecture",
                "requirement": json.dumps(
                    project.backend_architecture,
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
        implementation_context = {
            "mandatory_rules": mandatory_rules,
            "architecture_artifact": architecture.to_dict(),
            "backend_requirements": cls._backend_requirements(architecture),
            "module_packages": {
                module: f"backend/{_python_package_name(module)}"
                for module in cls._backend_requirements(architecture)[
                    "modules"
                ]
            },
            "authorized_dependencies": cls._declared_dependencies(project),
            "persistence_dependencies": cls._declared_dependencies(
                project,
                PERSISTENCE_DEPENDENCY_KEYS,
            ),
            "existing_files": [
                {
                    "path": workspace_file.relative_path,
                    "content": workspace_file.content,
                }
                for workspace_file in existing_files
            ],
        }
        if (
            str(project.backend_stack.get("language", "")).casefold()
            == "python"
        ):
            implementation_context["python_generation_rules"] = [
                (
                    "Represent domain data models with standard-library "
                    "dataclasses unless an alternative modeling package is "
                    "explicitly authorized."
                ),
                (
                    "Use only the declared persistence library directly; "
                    "do not import an ORM or its transitive packages unless "
                    "the Project explicitly declares it."
                ),
                (
                    "Import declared package names literally; when psycopg "
                    "is declared, use import psycopg and never psycopg2."
                ),
                (
                    "Import every FastAPI symbol from fastapi in each Python "
                    "file where that symbol is referenced."
                ),
                (
                    "FastAPI route parameters may contain request data or "
                    "dependencies declared with Depends. Never annotate a "
                    "route parameter with a plain service class and default "
                    "it to None; instantiate that service inside the route "
                    "or inject it with Depends."
                ),
                (
                    "Use Python 3.12 PEP 604 union syntax such as Task | "
                    "None; never use typing.Optional."
                ),
            ]
        return implementation_context

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
            raise BackendGenerationValidationError(
                "Architecture artifact contradicts Project fields: "
                + ", ".join(mismatches)
            )

    @classmethod
    def _validate_backend_authority(
        cls,
        project: Project,
        generation: BackendGenerationArtifact,
    ) -> None:
        mismatches = [
            field_name
            for field_name, expected in cls._authoritative_backend(
                project
            ).items()
            if getattr(generation, field_name) != expected
        ]
        if mismatches:
            raise BackendGenerationValidationError(
                "Backend output contradicts authoritative Project fields: "
                + ", ".join(mismatches)
            )

    @classmethod
    def _backend_requirements(
        cls,
        architecture: ArchitectureArtifact,
    ) -> dict[str, list[str]]:
        modules_by_name = {
            module["name"]: module
            for module in architecture.modules
        }
        module_names = {
            api["provider"] for api in architecture.apis
        }
        module_names.update(
            interface["provider"] for interface in architecture.interfaces
        )
        module_names.update(
            store["owned_by"]
            for store in architecture.persistence.get("stores", [])
        )

        pending = list(module_names)
        while pending:
            module_name = pending.pop()
            for dependency in modules_by_name[module_name]["dependencies"]:
                if dependency not in module_names:
                    module_names.add(dependency)
                    pending.append(dependency)

        return {
            "modules": sorted(module_names),
            "interfaces": sorted(
                interface["name"]
                for interface in architecture.interfaces
                if interface["provider"] in module_names
            ),
            "apis": sorted(
                api["name"]
                for api in architecture.apis
                if api["provider"] in module_names
            ),
            "persistence_stores": sorted(
                store["name"]
                for store in architecture.persistence.get("stores", [])
                if store["owned_by"] in module_names
            ),
        }

    @staticmethod
    def _declared_dependencies(
        project: Project,
        keys: frozenset[str] = DEPENDENCY_KEYS,
    ) -> list[str]:
        dependencies = []

        def collect(value: Any) -> None:
            if isinstance(value, str) and value.strip():
                dependency = _dependency_root(value)
                if dependency:
                    dependencies.append(dependency)
            elif isinstance(value, list):
                for nested in value:
                    collect(nested)
            elif isinstance(value, dict):
                for nested in value.values():
                    collect(nested)

        for key, value in project.backend_stack.items():
            if key.casefold() in keys:
                collect(value)
        return list(dict.fromkeys(dependencies))

    @classmethod
    def _validate_complete_generation(
        cls,
        project: Project,
        architecture: ArchitectureArtifact,
        generation: BackendGenerationArtifact,
        existing_files: list[WorkspaceFile],
    ) -> None:
        requirements = cls._backend_requirements(architecture)
        implementation = generation.implementation
        for field_name, expected in requirements.items():
            actual = sorted(implementation[field_name])
            if actual != expected:
                raise BackendGenerationValidationError(
                    f"Backend implementation does not cover architecture "
                    f"{field_name}"
                )

        generated_paths = {
            workspace_file.relative_path for workspace_file in generation.files
        }
        if any(not path.startswith("backend/") for path in generated_paths):
            raise BackendGenerationValidationError(
                "Backend output files must stay under backend/"
            )

        authorized = set(cls._declared_dependencies(project))
        declared = {
            _dependency_root(dependency)
            for dependency in implementation["dependencies"]
        }
        if "" in declared or declared.difference(authorized):
            raise BackendGenerationValidationError(
                "Backend output uses undeclared dependencies"
            )

        combined_files = {
            workspace_file.relative_path: workspace_file
            for workspace_file in existing_files
            if workspace_file.relative_path.startswith("backend/")
        }
        combined_files.update(
            {
                workspace_file.relative_path: workspace_file
                for workspace_file in generation.files
            }
        )
        imported = _validate_python_workspace(
            combined_files,
            generated_paths,
            authorized,
        )
        if imported != declared:
            raise BackendGenerationValidationError(
                "Backend dependency manifest does not match source imports"
            )

        persistence_dependencies = set(
            cls._declared_dependencies(
                project,
                PERSISTENCE_DEPENDENCY_KEYS,
            )
        )
        if requirements["persistence_stores"] and not (
            persistence_dependencies.intersection(imported)
        ):
            raise BackendGenerationValidationError(
                "Backend persistence does not use its declared library"
            )

        source = "\n".join(
            workspace_file.content for workspace_file in generation.files
        )
        normalized_source = source.casefold()
        for interface in architecture.interfaces:
            if interface["name"] not in requirements["interfaces"]:
                continue
            for operation in interface["operations"]:
                if operation["name"].casefold() not in normalized_source:
                    raise BackendGenerationValidationError(
                        "Backend output does not implement architecture "
                        f"interface operation: {operation['name']}"
                    )
        for api in architecture.apis:
            if api["name"] not in requirements["apis"]:
                continue
            for operation in api["operations"]:
                method = operation["method"].casefold()
                path = operation["path"].casefold()
                if path not in normalized_source or not re.search(
                    rf"(?:\.|['\"])({re.escape(method)})(?:\(|['\"])",
                    normalized_source,
                ):
                    raise BackendGenerationValidationError(
                        "Backend output does not implement architecture API: "
                        f"{operation['method']} {operation['path']}"
                    )
        class_names = {
            node.name
            for workspace_file in generation.files
            if workspace_file.relative_path.endswith(".py")
            for node in ast.walk(
                ast.parse(
                    workspace_file.content,
                    filename=workspace_file.relative_path,
                )
            )
            if isinstance(node, ast.ClassDef)
        }
        for store in architecture.persistence.get("stores", []):
            if store["name"] not in requirements["persistence_stores"]:
                continue
            missing_models = {
                model["name"] for model in store["data_models"]
            }.difference(class_names)
            if missing_models:
                raise BackendGenerationValidationError(
                    "Backend output lacks persistence data models: "
                    + ", ".join(sorted(missing_models))
                )

    def _update_status(self, task: Task, status: TaskStatus) -> None:
        self.task_status_writer.update_status(task.id, status)
        task.status = status


def _dependency_root(value: str) -> str:
    candidate = re.split(r"[<>=!~\[\s]", value.strip(), maxsplit=1)[0]
    return candidate.replace("-", "_").casefold()


def _python_package_name(module_name: str) -> str:
    package = re.sub(r"[^a-zA-Z0-9_]", "_", module_name).casefold()
    if package[:1].isdigit():
        package = f"module_{package}"
    return package


def _validate_python_workspace(
    files: dict[str, WorkspaceFile],
    generated_paths: set[str],
    authorized_dependencies: set[str],
) -> set[str]:
    python_files = {
        path: workspace_file.content
        for path, workspace_file in files.items()
        if path.endswith(".py")
    }
    if not python_files:
        raise BackendGenerationValidationError(
            "Python backend output must contain Python source files"
        )

    module_index = _python_module_index(python_files)
    module_definitions = {
        module: _module_definitions(content, path)
        for module, (path, content) in module_index.items()
    }
    imported_dependencies = set()
    parsed_trees: dict[str, ast.Module] = {}
    for path, content in python_files.items():
        try:
            tree = ast.parse(content, filename=path)
            compile(content, path, "exec")
        except (SyntaxError, ValueError) as error:
            raise BackendGenerationValidationError(
                f"Backend Python source is invalid: {path}: {error}"
            ) from error
        parsed_trees[path] = tree

        if path in generated_paths:
            _reject_placeholders(tree, content, path)
        _reject_undefined_globals(content, path)

        current_module = _canonical_module(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_dependencies.update(
                        _validate_import(
                            alias.name,
                            None,
                            module_index,
                            module_definitions,
                            authorized_dependencies,
                            path,
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                module = _absolute_import_module(current_module, node)
                for alias in node.names:
                    imported_dependencies.update(
                        _validate_import(
                            module,
                            alias.name,
                            module_index,
                            module_definitions,
                            authorized_dependencies,
                            path,
                        )
                    )
    _reject_incompatible_internal_constructor_calls(parsed_trees)
    _reject_invalid_fastapi_route_parameters(parsed_trees)
    return imported_dependencies


def _reject_invalid_fastapi_route_parameters(
    trees: dict[str, ast.Module],
) -> None:
    plain_classes = {
        node.name
        for tree in trees.values()
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and not node.bases
        and not any(
            _decorator_name(decorator) == "dataclass"
            for decorator in node.decorator_list
        )
    }
    if not plain_classes:
        return

    http_methods = {
        "delete",
        "get",
        "head",
        "options",
        "patch",
        "post",
        "put",
        "trace",
    }
    for path, tree in trees.items():
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not any(
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr.casefold() in http_methods
                for decorator in node.decorator_list
            ):
                continue
            arguments = [*node.args.posonlyargs, *node.args.args]
            defaults = [
                None,
            ] * (len(arguments) - len(node.args.defaults)) + list(
                node.args.defaults
            )
            parameters = [
                *zip(arguments, defaults, strict=True),
                *zip(
                    node.args.kwonlyargs,
                    node.args.kw_defaults,
                    strict=True,
                ),
            ]
            for argument, default in parameters:
                annotation_names = {
                    child.id
                    for child in ast.walk(argument.annotation)
                    if isinstance(child, ast.Name)
                } if argument.annotation is not None else set()
                invalid_classes = annotation_names.intersection(
                    plain_classes
                )
                if not invalid_classes or _is_depends_call(default):
                    continue
                raise BackendGenerationValidationError(
                    "Backend FastAPI route parameter "
                    f"{argument.arg} in {path} uses plain service class "
                    f"{sorted(invalid_classes)[0]}; instantiate it inside "
                    "the route or inject it with fastapi.Depends"
                )


def _is_depends_call(node: ast.expr | None) -> bool:
    return (
        isinstance(node, ast.Call)
        and _decorator_name(node.func) == "Depends"
    )


def _reject_incompatible_internal_constructor_calls(
    trees: dict[str, ast.Module],
) -> None:
    constructors: dict[str, tuple[int, int | None, set[str]]] = {}
    duplicate_names: set[str] = set()
    for tree in trees.values():
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            signature = _local_constructor_signature(node)
            if signature is None:
                continue
            if node.name in constructors:
                duplicate_names.add(node.name)
            constructors[node.name] = signature

    for duplicate_name in duplicate_names:
        constructors.pop(duplicate_name, None)

    for path, tree in trees.items():
        for node in ast.walk(tree):
            if (
                not isinstance(node, ast.Call)
                or not isinstance(node.func, ast.Name)
                or node.func.id not in constructors
                or any(isinstance(argument, ast.Starred) for argument in node.args)
                or any(keyword.arg is None for keyword in node.keywords)
            ):
                continue
            minimum, maximum, keyword_names = constructors[node.func.id]
            positional_count = len(node.args)
            supplied_names = {
                keyword.arg for keyword in node.keywords if keyword.arg
            }
            supplied_count = positional_count + len(
                supplied_names.intersection(keyword_names)
            )
            unknown_keywords = supplied_names.difference(keyword_names)
            if (
                supplied_count < minimum
                or (maximum is not None and positional_count > maximum)
                or unknown_keywords
            ):
                raise BackendGenerationValidationError(
                    "Backend source calls incompatible internal constructor "
                    f"{node.func.id} in {path}"
                )


def _local_constructor_signature(
    class_node: ast.ClassDef,
) -> tuple[int, int | None, set[str]] | None:
    initializer = next(
        (
            node
            for node in class_node.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "__init__"
        ),
        None,
    )
    if initializer is not None:
        positional = [
            *initializer.args.posonlyargs,
            *initializer.args.args,
        ][1:]
        default_count = len(initializer.args.defaults)
        minimum = max(len(positional) - default_count, 0)
        maximum = None if initializer.args.vararg else len(positional)
        keyword_names = {
            argument.arg
            for argument in [
                *positional,
                *initializer.args.kwonlyargs,
            ]
        }
        return minimum, maximum, keyword_names

    dataclass_decorator = next(
        (
            decorator
            for decorator in class_node.decorator_list
            if _decorator_name(decorator) == "dataclass"
        ),
        None,
    )
    if dataclass_decorator is not None:
        if (
            isinstance(dataclass_decorator, ast.Call)
            and any(
                keyword.arg == "init"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
                for keyword in dataclass_decorator.keywords
            )
        ):
            return 0, 0, set()
        fields = [
            node
            for node in class_node.body
            if isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
        ]
        field_names = {node.target.id for node in fields}
        minimum = sum(node.value is None for node in fields)
        keyword_only = (
            isinstance(dataclass_decorator, ast.Call)
            and any(
                keyword.arg == "kw_only"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is True
                for keyword in dataclass_decorator.keywords
            )
        )
        return minimum, 0 if keyword_only else len(fields), field_names

    if not class_node.bases:
        return 0, 0, set()
    return None


def _decorator_name(decorator: ast.expr) -> str | None:
    target = decorator.func if isinstance(decorator, ast.Call) else decorator
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _python_module_index(
    python_files: dict[str, str],
) -> dict[str, tuple[str, str]]:
    index = {}
    for path, content in python_files.items():
        canonical = _canonical_module(path)
        aliases = {canonical}
        if canonical.startswith("backend."):
            aliases.add(canonical.removeprefix("backend."))
        if canonical.startswith("backend.src."):
            aliases.add(canonical.removeprefix("backend.src."))
        for alias in aliases:
            index[alias] = (path, content)
    return index


def _canonical_module(path: str) -> str:
    module = path[:-3].replace("/", ".")
    return module.removesuffix(".__init__")


def _module_definitions(content: str, path: str) -> set[str]:
    try:
        tree = ast.parse(content, filename=path)
    except SyntaxError as error:
        raise BackendGenerationValidationError(
            f"Backend Python source is invalid: {path}: {error}"
        ) from error
    definitions = set()
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                definitions.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            definitions.update(
                target.id for target in targets if isinstance(target, ast.Name)
            )
    return definitions


def _absolute_import_module(current_module: str, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""
    package_parts = current_module.split(".")[:-1]
    keep = len(package_parts) - node.level + 1
    prefix = package_parts[: max(keep, 0)]
    if node.module:
        prefix.extend(node.module.split("."))
    return ".".join(prefix)


def _validate_import(
    module: str,
    imported_name: str | None,
    module_index: dict[str, tuple[str, str]],
    module_definitions: dict[str, set[str]],
    authorized_dependencies: set[str],
    path: str,
) -> set[str]:
    if not module:
        raise BackendGenerationValidationError(
            f"Backend source contains unresolved import in {path}"
        )
    if module in module_index:
        if (
            imported_name
            and imported_name != "*"
            and imported_name not in module_definitions[module]
            and f"{module}.{imported_name}" not in module_index
        ):
            raise BackendGenerationValidationError(
                f"Backend source imports missing reference "
                f"{module}.{imported_name} in {path}"
            )
        return set()
    if any(indexed.startswith(module + ".") for indexed in module_index):
        if imported_name and f"{module}.{imported_name}" not in module_index:
            raise BackendGenerationValidationError(
                f"Backend source imports missing module "
                f"{module}.{imported_name} in {path}"
            )
        return set()

    root = module.split(".")[0].casefold()
    if root in sys.stdlib_module_names:
        return set()
    if root in authorized_dependencies:
        return {root}
    raise BackendGenerationValidationError(
        f"Backend source imports undeclared or missing dependency "
        f"{module} in {path}"
    )


def _reject_placeholders(tree: ast.AST, content: str, path: str) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Pass) or (
            isinstance(node, ast.Constant) and node.value is Ellipsis
        ):
            raise BackendGenerationValidationError(
                f"Backend source contains placeholder code: {path}"
            )
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            if isinstance(node.exc.func, ast.Name) and (
                node.exc.func.id == "NotImplementedError"
            ):
                raise BackendGenerationValidationError(
                    f"Backend source contains NotImplementedError: {path}"
                )
    try:
        comments = (
            token.string.casefold()
            for token in tokenize.generate_tokens(StringIO(content).readline)
            if token.type == tokenize.COMMENT
        )
        if any(
            marker in comment
            for comment in comments
            for marker in PLACEHOLDER_MARKERS
        ):
            raise BackendGenerationValidationError(
                f"Backend source contains placeholder comments: {path}"
            )
    except tokenize.TokenError as error:
        raise BackendGenerationValidationError(
            f"Backend source cannot be tokenized: {path}"
        ) from error


def _reject_undefined_globals(content: str, path: str) -> None:
    try:
        table = symtable.symtable(content, path, "exec")
    except SyntaxError as error:
        raise BackendGenerationValidationError(
            f"Backend source symbol table is invalid: {path}"
        ) from error
    module_definitions = {
        symbol.get_name()
        for symbol in table.get_symbols()
        if symbol.is_assigned()
        or symbol.is_imported()
        or symbol.is_namespace()
    }
    allowed = module_definitions | set(dir(builtins)) | {"__name__"}
    for scope in _walk_symbol_tables(table):
        undefined = {
            symbol.get_name()
            for symbol in scope.get_symbols()
            if symbol.is_referenced()
            and symbol.is_global()
            and symbol.get_name() not in allowed
        }
        if undefined:
            raise BackendGenerationValidationError(
                f"Backend source contains undefined references in {path}: "
                + ", ".join(sorted(undefined))
            )


def _walk_symbol_tables(table: symtable.SymbolTable):
    yield table
    for child in table.get_children():
        yield from _walk_symbol_tables(child)
