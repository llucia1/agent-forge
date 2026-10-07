import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ArchitectureArtifactValidationError(ValueError):
    """Raised when an architecture engine output violates its contract."""


class ArchitectureStatus(StrEnum):
    COMPLETE = "complete"
    NEEDS_INPUT = "needs_input"


@dataclass(frozen=True)
class ArchitectureArtifact:
    version: int
    status: ArchitectureStatus
    missing_decisions: list[str]
    backend_stack: dict[str, Any]
    backend_architecture: dict[str, Any]
    frontend_stack: dict[str, Any]
    frontend_architecture: dict[str, Any]
    infrastructure: dict[str, Any]
    technical_constraints: list[str]
    modules: list[dict[str, Any]]
    interfaces: list[dict[str, Any]]
    apis: list[dict[str, Any]]
    persistence: dict[str, Any]
    execution_plan: list[dict[str, Any]]

    @classmethod
    def from_json(cls, raw_output: str) -> "ArchitectureArtifact":
        try:
            payload = json.loads(
                raw_output,
                parse_constant=_reject_non_json_constant,
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ArchitectureArtifactValidationError(
                "Architecture output must be strict JSON"
            ) from error

        if not isinstance(payload, dict):
            raise ArchitectureArtifactValidationError(
                "Architecture output must be a JSON object"
            )

        required_fields = set(cls.required_fields())
        missing_fields = sorted(required_fields.difference(payload))
        if missing_fields:
            raise ArchitectureArtifactValidationError(
                "Architecture output is missing required fields: "
                + ", ".join(missing_fields)
            )

        unknown_fields = sorted(set(payload).difference(required_fields))
        if unknown_fields:
            raise ArchitectureArtifactValidationError(
                "Architecture output contains unknown fields: "
                + ", ".join(unknown_fields)
            )

        if type(payload["version"]) is not int or payload["version"] != 1:
            raise ArchitectureArtifactValidationError(
                "Architecture output version must be the integer 1"
            )

        try:
            status = ArchitectureStatus(payload["status"])
        except (TypeError, ValueError) as error:
            raise ArchitectureArtifactValidationError(
                "Architecture output status must be complete or needs_input"
            ) from error

        missing_decisions = payload["missing_decisions"]
        if not isinstance(missing_decisions, list) or not all(
            isinstance(item, str) and item.strip()
            for item in missing_decisions
        ):
            raise ArchitectureArtifactValidationError(
                "Architecture field missing_decisions must be "
                "an array of non-empty strings"
            )
        if len(set(missing_decisions)) != len(missing_decisions):
            raise ArchitectureArtifactValidationError(
                "Architecture field missing_decisions must not "
                "contain duplicates"
            )

        object_fields = (
            "backend_stack",
            "backend_architecture",
            "frontend_stack",
            "frontend_architecture",
            "infrastructure",
            "persistence",
        )
        for field_name in object_fields:
            if not isinstance(payload[field_name], dict):
                raise ArchitectureArtifactValidationError(
                    f"Architecture field {field_name} must be an object"
                )

        constraints = payload["technical_constraints"]
        if not isinstance(constraints, list) or not all(
            isinstance(item, str) for item in constraints
        ):
            raise ArchitectureArtifactValidationError(
                "Architecture field technical_constraints must be "
                "an array of strings"
            )

        object_list_fields = (
            "modules",
            "interfaces",
            "apis",
            "execution_plan",
        )
        for field_name in object_list_fields:
            value = payload[field_name]
            if not isinstance(value, list) or not all(
                isinstance(item, dict) for item in value
            ):
                raise ArchitectureArtifactValidationError(
                    f"Architecture field {field_name} must be "
                    "an array of objects"
                )

        if status is ArchitectureStatus.COMPLETE and missing_decisions:
            raise ArchitectureArtifactValidationError(
                "A complete architecture cannot have missing decisions"
            )
        if status is ArchitectureStatus.COMPLETE:
            empty_fields = [
                field_name
                for field_name in (
                    "modules",
                    "interfaces",
                    "apis",
                    "execution_plan",
                )
                if not payload[field_name]
            ]
            if empty_fields:
                raise ArchitectureArtifactValidationError(
                    "A complete architecture requires non-empty fields: "
                    + ", ".join(empty_fields)
                )
        module_names = _validate_modules(payload["modules"])
        _validate_interfaces(payload["interfaces"], module_names)
        _validate_apis(payload["apis"], module_names)
        _validate_persistence(payload["persistence"], module_names)
        _validate_execution_plan(payload["execution_plan"], module_names)
        if status is ArchitectureStatus.NEEDS_INPUT:
            if not missing_decisions:
                raise ArchitectureArtifactValidationError(
                    "A needs_input architecture requires missing decisions"
                )
            definitive_fields = (
                payload["modules"],
                payload["interfaces"],
                payload["apis"],
                payload["persistence"],
                payload["execution_plan"],
            )
            if any(definitive_fields):
                raise ArchitectureArtifactValidationError(
                    "A needs_input architecture cannot contain a "
                    "definitive design"
                )

        return cls(
            version=payload["version"],
            status=status,
            missing_decisions=missing_decisions,
            backend_stack=payload["backend_stack"],
            backend_architecture=payload["backend_architecture"],
            frontend_stack=payload["frontend_stack"],
            frontend_architecture=payload["frontend_architecture"],
            infrastructure=payload["infrastructure"],
            technical_constraints=constraints,
            modules=payload["modules"],
            interfaces=payload["interfaces"],
            apis=payload["apis"],
            persistence=payload["persistence"],
            execution_plan=payload["execution_plan"],
        )

    @staticmethod
    def required_fields() -> tuple[str, ...]:
        return (
            "version",
            "status",
            "missing_decisions",
            "backend_stack",
            "backend_architecture",
            "frontend_stack",
            "frontend_architecture",
            "infrastructure",
            "technical_constraints",
            "modules",
            "interfaces",
            "apis",
            "persistence",
            "execution_plan",
        )

    @classmethod
    def output_contract(cls) -> dict[str, Any]:
        object_schema = {"type": "object"}
        non_empty_string = {"type": "string", "minLength": 1}
        module_schema = {
            "type": "object",
            "required": [
                "name",
                "responsibility",
                "layer",
                "dependencies",
            ],
            "properties": {
                "name": non_empty_string,
                "responsibility": non_empty_string,
                "layer": non_empty_string,
                "dependencies": {
                    "type": "array",
                    "items": non_empty_string,
                    "uniqueItems": True,
                },
            },
            "additionalProperties": False,
        }
        interface_schema = {
            "type": "object",
            "required": ["name", "provider", "consumers", "operations"],
            "properties": {
                "name": non_empty_string,
                "provider": non_empty_string,
                "consumers": {
                    "type": "array",
                    "minItems": 1,
                    "items": non_empty_string,
                    "uniqueItems": True,
                },
                "operations": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": ["name", "input", "output"],
                        "properties": {
                            "name": non_empty_string,
                            "input": object_schema,
                            "output": object_schema,
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }
        api_schema = {
            "type": "object",
            "required": [
                "name",
                "protocol",
                "provider",
                "consumers",
                "operations",
            ],
            "properties": {
                "name": non_empty_string,
                "protocol": non_empty_string,
                "provider": non_empty_string,
                "consumers": {
                    "type": "array",
                    "minItems": 1,
                    "items": non_empty_string,
                    "uniqueItems": True,
                },
                "operations": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "name",
                            "method",
                            "path",
                            "request",
                            "responses",
                        ],
                        "properties": {
                            "name": non_empty_string,
                            "method": non_empty_string,
                            "path": non_empty_string,
                            "request": object_schema,
                            "responses": {
                                "type": "object",
                                "minProperties": 1,
                            },
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        }
        persistence_schema = {
            "type": "object",
            "properties": {
                "stores": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "required": [
                            "name",
                            "technology",
                            "purpose",
                            "owned_by",
                            "data_models",
                        ],
                        "properties": {
                            "name": non_empty_string,
                            "technology": non_empty_string,
                            "purpose": non_empty_string,
                            "owned_by": non_empty_string,
                            "data_models": {
                                "type": "array",
                                "minItems": 1,
                                "items": {
                                    "type": "object",
                                    "required": ["name", "description"],
                                    "properties": {
                                        "name": non_empty_string,
                                        "description": non_empty_string,
                                    },
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "additionalProperties": False,
                    },
                }
            },
            "additionalProperties": False,
        }
        execution_step_schema = {
            "type": "object",
            "required": ["order", "name", "description", "modules"],
            "properties": {
                "order": {"type": "integer", "minimum": 1},
                "name": non_empty_string,
                "description": non_empty_string,
                "modules": {
                    "type": "array",
                    "minItems": 1,
                    "items": non_empty_string,
                    "uniqueItems": True,
                },
            },
            "additionalProperties": False,
        }
        return {
            "instruction": (
                "Return only one strict JSON object. Do not use Markdown "
                "or code fences."
            ),
            "type": "object",
            "required": list(cls.required_fields()),
            "properties": {
                "version": {"type": "integer", "const": 1},
                "status": {
                    "type": "string",
                    "enum": ["complete", "needs_input"],
                },
                "missing_decisions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "backend_stack": object_schema,
                "backend_architecture": object_schema,
                "frontend_stack": object_schema,
                "frontend_architecture": object_schema,
                "infrastructure": object_schema,
                "technical_constraints": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "modules": {"type": "array", "items": module_schema},
                "interfaces": {"type": "array", "items": interface_schema},
                "apis": {"type": "array", "items": api_schema},
                "persistence": persistence_schema,
                "execution_plan": {
                    "type": "array",
                    "items": execution_step_schema,
                },
            },
            "allOf": [
                {
                    "if": {
                        "properties": {"status": {"const": "complete"}}
                    },
                    "then": {
                        "properties": {
                            field_name: {"minItems": 1}
                            for field_name in (
                                "modules",
                                "interfaces",
                                "apis",
                                "execution_plan",
                            )
                        }
                    },
                }
            ],
            "additionalProperties": False,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "status": str(self.status),
            "missing_decisions": self.missing_decisions,
            "backend_stack": self.backend_stack,
            "backend_architecture": self.backend_architecture,
            "frontend_stack": self.frontend_stack,
            "frontend_architecture": self.frontend_architecture,
            "infrastructure": self.infrastructure,
            "technical_constraints": self.technical_constraints,
            "modules": self.modules,
            "interfaces": self.interfaces,
            "apis": self.apis,
            "persistence": self.persistence,
            "execution_plan": self.execution_plan,
        }

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _reject_non_json_constant(value: str) -> None:
    raise ValueError(f"Non-JSON numeric constant: {value}")


def _require_exact_fields(
    value: dict[str, Any],
    required: set[str],
    field_name: str,
) -> None:
    if set(value) != required:
        raise ArchitectureArtifactValidationError(
            f"Architecture {field_name} must contain exactly: "
            + ", ".join(sorted(required))
        )


def _require_non_empty_string(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ArchitectureArtifactValidationError(
            f"Architecture {field_name} must be a non-empty string"
        )
    return value


def _require_string_list(
    value: Any,
    field_name: str,
    *,
    allow_empty: bool,
) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or not all(isinstance(item, str) and item.strip() for item in value)
        or len(set(value)) != len(value)
    ):
        qualifier = "" if allow_empty else " non-empty"
        raise ArchitectureArtifactValidationError(
            f"Architecture {field_name} must be a{qualifier} array of "
            "unique non-empty strings"
        )
    return value


def _validate_modules(modules: list[dict[str, Any]]) -> set[str]:
    required = {"name", "responsibility", "layer", "dependencies"}
    names = set()
    for index, module in enumerate(modules):
        field_name = f"modules[{index}]"
        _require_exact_fields(module, required, field_name)
        name = _require_non_empty_string(module["name"], f"{field_name}.name")
        _require_non_empty_string(
            module["responsibility"],
            f"{field_name}.responsibility",
        )
        _require_non_empty_string(module["layer"], f"{field_name}.layer")
        _require_string_list(
            module["dependencies"],
            f"{field_name}.dependencies",
            allow_empty=True,
        )
        if name in names:
            raise ArchitectureArtifactValidationError(
                "Architecture module names must be unique"
            )
        names.add(name)

    for index, module in enumerate(modules):
        dependencies = set(module["dependencies"])
        if (
            dependencies.difference(names)
            or module["name"] in dependencies
        ):
            raise ArchitectureArtifactValidationError(
                f"Architecture modules[{index}].dependencies must reference "
                "other declared modules"
            )
    return names


def _validate_interfaces(
    interfaces: list[dict[str, Any]],
    module_names: set[str],
) -> None:
    required = {"name", "provider", "consumers", "operations"}
    operation_required = {"name", "input", "output"}
    names = set()
    for index, interface in enumerate(interfaces):
        field_name = f"interfaces[{index}]"
        _require_exact_fields(interface, required, field_name)
        name = _require_non_empty_string(
            interface["name"],
            f"{field_name}.name",
        )
        provider = _require_non_empty_string(
            interface["provider"],
            f"{field_name}.provider",
        )
        consumers = _require_string_list(
            interface["consumers"],
            f"{field_name}.consumers",
            allow_empty=False,
        )
        operations = interface["operations"]
        if not isinstance(operations, list) or not operations:
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name}.operations must be a non-empty "
                "array"
            )
        for operation_index, operation in enumerate(operations):
            operation_name = f"{field_name}.operations[{operation_index}]"
            if not isinstance(operation, dict):
                raise ArchitectureArtifactValidationError(
                    f"Architecture {operation_name} must be an object"
                )
            _require_exact_fields(operation, operation_required, operation_name)
            _require_non_empty_string(
                operation["name"],
                f"{operation_name}.name",
            )
            if not isinstance(operation["input"], dict) or not isinstance(
                operation["output"],
                dict,
            ):
                raise ArchitectureArtifactValidationError(
                    f"Architecture {operation_name} input and output must "
                    "be objects"
                )
        if name in names:
            raise ArchitectureArtifactValidationError(
                "Architecture interface names must be unique"
            )
        names.add(name)
        referenced = {provider, *consumers}
        if referenced.difference(module_names) or provider in consumers:
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name} must connect distinct declared "
                "modules"
            )


def _validate_apis(
    apis: list[dict[str, Any]],
    module_names: set[str],
) -> None:
    required = {"name", "protocol", "provider", "consumers", "operations"}
    operation_required = {
        "name",
        "method",
        "path",
        "request",
        "responses",
    }
    names = set()
    for index, api in enumerate(apis):
        field_name = f"apis[{index}]"
        _require_exact_fields(api, required, field_name)
        name = _require_non_empty_string(api["name"], f"{field_name}.name")
        _require_non_empty_string(
            api["protocol"],
            f"{field_name}.protocol",
        )
        provider = _require_non_empty_string(
            api["provider"],
            f"{field_name}.provider",
        )
        consumers = _require_string_list(
            api["consumers"],
            f"{field_name}.consumers",
            allow_empty=False,
        )
        operations = api["operations"]
        if not isinstance(operations, list) or not operations:
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name}.operations must be a non-empty "
                "array"
            )
        for operation_index, operation in enumerate(operations):
            operation_name = f"{field_name}.operations[{operation_index}]"
            if not isinstance(operation, dict):
                raise ArchitectureArtifactValidationError(
                    f"Architecture {operation_name} must be an object"
                )
            _require_exact_fields(operation, operation_required, operation_name)
            for key in ("name", "method", "path"):
                _require_non_empty_string(
                    operation[key],
                    f"{operation_name}.{key}",
                )
            if (
                not isinstance(operation["request"], dict)
                or not isinstance(operation["responses"], dict)
                or not operation["responses"]
            ):
                raise ArchitectureArtifactValidationError(
                    f"Architecture {operation_name} request must be an "
                    "object and responses must be a non-empty object"
                )
        if name in names:
            raise ArchitectureArtifactValidationError(
                "Architecture API names must be unique"
            )
        names.add(name)
        referenced = {provider, *consumers}
        if referenced.difference(module_names) or provider in consumers:
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name} must connect distinct declared "
                "modules"
            )


def _validate_persistence(
    persistence: dict[str, Any],
    module_names: set[str],
) -> None:
    if not persistence:
        return
    _require_exact_fields(persistence, {"stores"}, "persistence")
    stores = persistence["stores"]
    if not isinstance(stores, list) or not stores:
        raise ArchitectureArtifactValidationError(
            "Architecture persistence.stores must be a non-empty array"
        )
    store_required = {
        "name",
        "technology",
        "purpose",
        "owned_by",
        "data_models",
    }
    model_required = {"name", "description"}
    names = set()
    for index, store in enumerate(stores):
        field_name = f"persistence.stores[{index}]"
        if not isinstance(store, dict):
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name} must be an object"
            )
        _require_exact_fields(store, store_required, field_name)
        name = _require_non_empty_string(store["name"], f"{field_name}.name")
        for key in ("technology", "purpose", "owned_by"):
            _require_non_empty_string(store[key], f"{field_name}.{key}")
        if store["owned_by"] not in module_names:
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name}.owned_by must reference a "
                "declared module"
            )
        models = store["data_models"]
        if not isinstance(models, list) or not models:
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name}.data_models must be a non-empty "
                "array"
            )
        for model_index, model in enumerate(models):
            model_name = f"{field_name}.data_models[{model_index}]"
            if not isinstance(model, dict):
                raise ArchitectureArtifactValidationError(
                    f"Architecture {model_name} must be an object"
                )
            _require_exact_fields(model, model_required, model_name)
            _require_non_empty_string(model["name"], f"{model_name}.name")
            _require_non_empty_string(
                model["description"],
                f"{model_name}.description",
            )
        if name in names:
            raise ArchitectureArtifactValidationError(
                "Architecture persistence store names must be unique"
            )
        names.add(name)


def _validate_execution_plan(
    execution_plan: list[dict[str, Any]],
    module_names: set[str],
) -> None:
    required = {"order", "name", "description", "modules"}
    orders = []
    for index, step in enumerate(execution_plan):
        field_name = f"execution_plan[{index}]"
        _require_exact_fields(step, required, field_name)
        order = step["order"]
        if type(order) is not int or order <= 0:
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name}.order must be a positive integer"
            )
        orders.append(order)
        _require_non_empty_string(step["name"], f"{field_name}.name")
        _require_non_empty_string(
            step["description"],
            f"{field_name}.description",
        )
        modules = _require_string_list(
            step["modules"],
            f"{field_name}.modules",
            allow_empty=False,
        )
        if set(modules).difference(module_names):
            raise ArchitectureArtifactValidationError(
                f"Architecture {field_name}.modules must reference declared "
                "modules"
            )
    if orders and orders != list(range(1, len(orders) + 1)):
        raise ArchitectureArtifactValidationError(
            "Architecture execution plan orders must be sequential from 1"
        )
