import time

from agents.orchestrator.agent import OrchestratorAgent
from core.infrastructure.postgres import check_postgres
from core.infrastructure.rabbitmq import check_rabbitmq


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

    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()