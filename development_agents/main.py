from agents.architect.agent import ArchitectAgent
from agents.orchestrator.agent import OrchestratorAgent
from core.config import EngineSettings
from core.engines import (
    ClaudeEngine,
    CodexEngine,
    EngineResolver,
    LiteLLMEngine,
    ModelResolver,
)
from core.infrastructure.postgres import check_postgres
from core.infrastructure.rabbitmq import TaskConsumer, check_rabbitmq
from core.infrastructure.repositories.project_repository import ProjectRepository
from core.infrastructure.repositories.task_repository import TaskRepository
from core.memory import DefaultContextProvider


def main():
    print("AgentForge started", flush=True)
    print(f"PostgreSQL: {check_postgres()}", flush=True)
    print(f"RabbitMQ: {check_rabbitmq()}", flush=True)

    agent = OrchestratorAgent()

    project = agent.orchestrator.create_project(
        name="Demo Project",
        description="Proyecto de prueba de AgentForge",
    )

    task = agent.orchestrator.create_task(
        project=project,
        title="Define architecture",
        description="Define initial project architecture",
        agent="architect",
    )

    print(project, flush=True)
    print(task, flush=True)

    engine_settings = EngineSettings.from_environment()
    model_resolver = ModelResolver(
        default_model=engine_settings.default_model,
        models_by_agent=engine_settings.models_by_agent,
        models_by_project=engine_settings.models_by_project,
    )
    engine = EngineResolver(
        engines={
            "codex": CodexEngine(),
            "claude": ClaudeEngine(),
            "litellm": LiteLLMEngine.from_environment(model_resolver),
        },
        default_engine=engine_settings.default_engine,
    )
    architect_agent = ArchitectAgent(
        task_repository=TaskRepository(),
        project_repository=ProjectRepository(),
        engine=engine,
        context_provider=DefaultContextProvider(),
    )
    TaskConsumer().consume("architect", architect_agent.handle)


if __name__ == "__main__":
    main()
