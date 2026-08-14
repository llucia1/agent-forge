from pathlib import Path

from agents.architect.agent import ArchitectAgent
from agents.backend.agent import BackendAgent
from agents.orchestrator.agent import OrchestratorAgent
from core.config import load_settings
from core.engines.model_resolver import ModelResolver
from core.engines.resolver import EngineResolver
from core.infrastructure.engines.claude import ClaudeEngine
from core.infrastructure.engines.codex import CodexEngine
from core.infrastructure.engines.litellm import LiteLLMEngine
from core.infrastructure.postgres import check_postgres
from core.infrastructure.rabbitmq import (
    RabbitMQTaskConsumer,
    RabbitMQTaskPublisher,
    check_rabbitmq,
)
from core.infrastructure.repositories.project_repository import (
    PostgresProjectRepository,
)
from core.infrastructure.repositories.task_repository import (
    PostgresTaskRepository,
)
from core.infrastructure.repositories.task_result_repository import (
    PostgresTaskResultRepository,
)
from core.infrastructure.workspace import FilesystemProjectWorkspace
from core.memory.default_context import DefaultContextProvider
from core.models.task import AgentRole
from core.orchestration.orchestrator import Orchestrator


def main():
    settings = load_settings()

    print("AgentForge started", flush=True)
    print(f"PostgreSQL: {check_postgres(settings.database)}", flush=True)
    print(f"RabbitMQ: {check_rabbitmq(settings.rabbitmq)}", flush=True)

    project_repository = PostgresProjectRepository(settings.database)
    task_repository = PostgresTaskRepository(settings.database)
    task_result_repository = PostgresTaskResultRepository(settings.database)
    project_workspace = FilesystemProjectWorkspace(Path("/workspaces"))
    task_publisher = RabbitMQTaskPublisher(settings.rabbitmq)
    task_consumer = RabbitMQTaskConsumer(settings.rabbitmq)
    orchestration = Orchestrator(
        project_creator=project_repository,
        task_creator=task_repository,
        task_publisher=task_publisher,
        workspace_initializer=project_workspace,
    )
    orchestrator_agent = OrchestratorAgent(orchestration)
    model_resolver = ModelResolver(
        default_model=settings.engine.default_model,
        models_by_agent=settings.engine.models_by_agent,
        models_by_project=settings.engine.models_by_project,
    )
    engine = EngineResolver(
        engines={
            "codex": CodexEngine(),
            "claude": ClaudeEngine(),
            "litellm": LiteLLMEngine(model_resolver, settings.litellm),
        },
        default_engine=settings.engine.default_engine,
    )
    architect_agent = ArchitectAgent(
        task_status_writer=task_repository,
        task_result_writer=task_result_repository,
        architecture_artifact_writer=project_workspace,
        project_reader=project_repository,
        engine=engine,
        context_provider=DefaultContextProvider(),
    )
    backend_agent = BackendAgent(
        task_status_writer=task_repository,
        task_result_writer=task_result_repository,
        project_reader=project_repository,
        workspace=project_workspace,
        engine=engine,
        context_provider=DefaultContextProvider(),
    )

    project = orchestrator_agent.create_project(
        name="Demo Project",
        description="Proyecto de prueba de AgentForge",
    )

    task = orchestrator_agent.create_task(
        project=project,
        title="Define architecture",
        description="Define initial project architecture",
        agent=AgentRole.ARCHITECT,
    )

    print(project, flush=True)
    print(task, flush=True)

    task_consumer.consume(
        {
            AgentRole.ARCHITECT: architect_agent,
            AgentRole.BACKEND: backend_agent,
        }
    )


if __name__ == "__main__":
    main()
