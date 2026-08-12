from uuid import UUID

from core.models.project import Project
from core.models.task import Task


class ModelConfigurationError(LookupError):
    """Raised when model selection does not resolve a valid alias."""


class ModelResolver:
    def __init__(
        self,
        default_model: str,
        models_by_agent: dict[str, str] | None = None,
        models_by_project: dict[UUID, str] | None = None,
    ):
        self.default_model = default_model
        self.models_by_agent = dict(models_by_agent or {})
        self.models_by_project = dict(models_by_project or {})

    def resolve(self, task: Task, project: Project) -> str:
        model_alias = self.models_by_project.get(project.id)

        if model_alias is None:
            model_alias = self.models_by_agent.get(task.agent)

        if model_alias is None:
            model_alias = self.default_model

        if not isinstance(model_alias, str) or not model_alias.strip():
            raise ModelConfigurationError(
                "Model selection did not resolve a valid alias"
            )

        return model_alias.strip()
