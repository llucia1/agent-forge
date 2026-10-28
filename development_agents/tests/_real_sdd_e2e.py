import json
import sys
from pathlib import Path
from uuid import UUID

import pika

from agents.architect.agent import ArchitectAgent
from agents.backend.agent import BackendAgent
from agents.frontend.agent import FrontendAgent
from agents.qa.agent import QAAgent
from agents.reviewer.agent import ReviewerAgent
from core.config import load_settings
from core.contracts.configuration import RabbitMQSettings
from core.engines.model_resolver import ModelResolver
from core.infrastructure.engines.litellm import LiteLLMEngine
from core.infrastructure.qa_executor import IsolatedProjectQAExecutor
from core.infrastructure.rabbitmq import (
    RabbitMQTaskConsumer,
    RabbitMQTaskPublisher,
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
from core.models.qa import (
    ExecutableRule,
    QACheck,
    QACommand,
    QAExecutionPolicy,
)
from core.models.task import AgentRole
from core.models.workspace import ProjectSnapshot
from core.orchestration.orchestrator import Orchestrator


PROJECT_MARKDOWN = """# Real SDD Status

## Objective
Expose application status through a backend API and a frontend client.

## Scope
One status feature implemented without undeclared dependencies.

## Global Rules
- Use only Project technologies and architecture.json.
- Generate complete buildable source without placeholders.
"""

SPEC_MARKDOWN = """# Read application status

## Objective
Return the current application status to the frontend.

## Scope
Backend status endpoint and frontend client integration.

## Business Rules
- The successful status value is ready.

## Flows
1. The frontend requests status. 2. The backend returns the status.

## APIs and Interfaces
- GET /status returns an object containing status.

## Data
- StatusResponse contains status as a string.

## Errors
- Transport failures are reported to the caller.

## Edge Cases
- Repeated status requests return the same successful value.

## Security
- The endpoint accepts no user-controlled input.

## Acceptance Criteria
- AC-001 [check: backend-compile]: All generated backend Python source compiles.
"""


def components(run_id: str):
    settings = load_settings()
    queues = {
        role: f"e2e.sdd.real.{run_id}.{role.value}"
        for role in AgentRole
    }
    rabbit = RabbitMQSettings(
        settings.rabbitmq.host,
        settings.rabbitmq.port,
        settings.rabbitmq.user,
        settings.rabbitmq.password,
        queues,
    )
    projects = PostgresProjectRepository(settings.database)
    tasks = PostgresTaskRepository(settings.database)
    results = PostgresTaskResultRepository(settings.database)
    workspace = FilesystemProjectWorkspace(Path("/workspaces"))
    resolver = ModelResolver(
        settings.engine.default_model,
        settings.engine.models_by_agent,
        settings.engine.models_by_project,
    )
    engine = RecordingEngine(LiteLLMEngine(resolver, settings.litellm))
    return settings, rabbit, projects, tasks, results, workspace, engine


class RecordingEngine:
    def __init__(self, delegate):
        self.delegate = delegate
        self.calls = {}
        self.outputs = {}

    def run(self, task, project, context=None):
        role = str(task.agent)
        self.calls[role] = self.calls.get(role, 0) + 1
        print(
            json.dumps(
                {"event": "model_call", "agent": role, "attempt": self.calls[role]}
            ),
            flush=True,
        )
        result = self.delegate.run(task, project, context)
        self.outputs.setdefault(role, []).append(result.output)
        return result


def orchestration(rabbit, projects, tasks, workspace):
    return Orchestrator(
        projects,
        tasks,
        RabbitMQTaskPublisher(rabbit),
        workspace,
        projects,
        projects,
        tasks,
        workspace,
        workspace,
        workspace,
    )


def deliver(rabbit, task, handler):
    credentials = pika.PlainCredentials(rabbit.user, rabbit.password)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=rabbit.host,
            port=rabbit.port,
            credentials=credentials,
        )
    )
    channel = connection.channel()
    method, _, body = channel.basic_get(
        rabbit.task_queues[task.agent],
        auto_ack=True,
    )
    if method is None:
        raise RuntimeError(f"RabbitMQ task missing: {task.id}")
    delivered = RabbitMQTaskConsumer._deserialize_task(body)
    connection.close()
    if delivered.id != task.id or delivered.spec_path != task.spec_path:
        raise RuntimeError("RabbitMQ task contract changed in transit")
    handler.handle(delivered)
    print(
        json.dumps(
            {
                "event": "agent_complete",
                "agent": str(delivered.agent),
                "status": str(delivered.status),
            }
        ),
        flush=True,
    )
    return delivered


def generate(run_id: str):
    (
        _,
        rabbit,
        projects,
        tasks,
        results,
        workspace,
        engine,
    ) = components(run_id)
    flow = orchestration(rabbit, projects, tasks, workspace)
    project = flow.create_project(
        name=f"REAL SDD E2E {run_id}",
        description="Real all-agent SDD validation",
        backend_stack={
            "language": "Python",
            "runtime": "Python 3.12",
            "dependencies": [],
        },
        backend_architecture={"style": "layered"},
        frontend_stack={
            "language": "JavaScript",
            "runtime": "browser",
            "dependencies": [],
        },
        frontend_architecture={"style": "component-based"},
        infrastructure={"runtime": "Docker Compose"},
        technical_constraints=["Do not add undeclared dependencies"],
        project_markdown=PROJECT_MARKDOWN,
    )
    spec_path = flow.register_specification(
        project,
        "specs/read-status.md",
        SPEC_MARKDOWN,
    )
    context = DefaultContextProvider()
    handlers = {
        AgentRole.ARCHITECT: ArchitectAgent(
            tasks,
            results,
            workspace,
            projects,
            engine,
            context,
            workspace,
        ),
        AgentRole.BACKEND: BackendAgent(
            tasks,
            results,
            projects,
            workspace,
            engine,
            context,
        ),
        AgentRole.FRONTEND: FrontendAgent(
            tasks,
            results,
            projects,
            workspace,
            engine,
            context,
        ),
    }
    statuses = {}
    for role in (
        AgentRole.ARCHITECT,
        AgentRole.BACKEND,
        AgentRole.FRONTEND,
    ):
        task = flow.create_task(
            project,
            f"{role.value} status feature",
            "Implement specs/read-status.md",
            role,
            spec_path,
        )
        try:
            delivered = deliver(rabbit, task, handlers[role])
        except Exception as error:
            outputs = engine.outputs.get(str(role), [])
            print(
                json.dumps(
                    {
                        "event": "agent_failed",
                        "agent": str(role),
                        "error_type": type(error).__name__,
                        "error": str(error),
                        "last_model_output": outputs[-1] if outputs else None,
                    }
                ),
                flush=True,
            )
            raise
        statuses[role.value] = str(delivered.status)
    print(
        json.dumps(
            {
                "event": "generation_ready",
                "run_id": run_id,
                "project_id": str(project.id),
                "statuses": statuses,
                "model_calls": engine.calls,
                "workspace_files": sorted(
                    item.relative_path
                    for item in workspace.read_files(project.id)
                ),
            }
        ),
        flush=True,
    )


def finish(run_id: str, project_id: UUID):
    (
        _,
        rabbit,
        projects,
        tasks,
        results,
        workspace,
        engine,
    ) = components(run_id)
    project = projects.find_by_id(project_id)
    if project is None:
        raise RuntimeError(f"Project not found: {project_id}")
    flow = orchestration(rabbit, projects, tasks, workspace)
    context = DefaultContextProvider()
    reviewer = ReviewerAgent(
        tasks,
        results,
        projects,
        workspace,
        engine,
        context,
    )
    review_task = flow.create_task(
        project,
        "Review status feature",
        "Review architecture, spec and generated workspace",
        AgentRole.REVIEWER,
        "specs/read-status.md",
    )
    delivered_review = deliver(rabbit, review_task, reviewer)
    python_files = sorted(
        item.relative_path
        for item in workspace.read_files(project_id)
        if item.relative_path.startswith("backend/")
        and item.relative_path.endswith(".py")
    )
    if not python_files:
        raise RuntimeError("Generated backend contains no Python source")
    policy = QAExecutionPolicy(
        (
            ExecutableRule(
                "python3",
                (("-m", "compileall", "backend"),),
                (".",),
                30,
            ),
        ),
        65536,
        False,
    )
    check = QACheck(
        "backend-compile",
        QACommand("python3", ("-m", "compileall", "backend"), "."),
        tuple(python_files),
        30,
    )
    qa = QAAgent(
        tasks,
        results,
        results,
        projects,
        workspace,
        IsolatedProjectQAExecutor(policy),
        policy,
        (check,),
    )
    qa_task = flow.create_task(
        project,
        "QA status feature",
        "Validate acceptance criteria",
        AgentRole.QA,
        "specs/read-status.md",
    )
    delivered_qa = deliver(rabbit, qa_task, qa)
    architecture = workspace.read_architecture(project_id)
    files = workspace.read_files(project_id)
    snapshot = ProjectSnapshot.create(architecture, files)
    specification = workspace.read_specification(
        project_id,
        "specs/read-status.md",
    )
    latest_review = results.find_latest(project_id, AgentRole.REVIEWER)
    latest_qa = results.find_latest(project_id, AgentRole.QA)
    review_payload = json.loads(latest_review.result.output)
    qa_payload = json.loads(latest_qa.result.output)
    print(
        json.dumps(
            {
                "event": "flow_complete",
                "project_id": str(project_id),
                "review_task_status": str(delivered_review.status),
                "review_status": review_payload["status"],
                "review_findings": review_payload["findings"],
                "review_workspace_fingerprint": latest_review.result.metadata.get(
                    "workspace_fingerprint"
                ),
                "actual_workspace_fingerprint": snapshot.fingerprint,
                "review_spec_fingerprint": latest_review.result.metadata.get(
                    "spec_fingerprint"
                ),
                "actual_spec_fingerprint": specification.fingerprint,
                "qa_task_status": str(delivered_qa.status),
                "qa_status": qa_payload["status"],
                "qa_checks": qa_payload["checks"],
                "review_model_calls": engine.calls,
            }
        ),
        flush=True,
    )


def cleanup(run_id: str, project_id: UUID):
    _, rabbit, projects, _, _, workspace, _ = components(run_id)
    workspace.remove(project_id)
    projects.delete(project_id)
    credentials = pika.PlainCredentials(rabbit.user, rabbit.password)
    connection = pika.BlockingConnection(
        pika.ConnectionParameters(
            host=rabbit.host,
            port=rabbit.port,
            credentials=credentials,
        )
    )
    channel = connection.channel()
    for queue in rabbit.task_queues.values():
        channel.queue_delete(queue=queue)
    connection.close()
    print(json.dumps({"event": "cleanup_complete"}), flush=True)


if __name__ == "__main__":
    command = sys.argv[1]
    current_run = sys.argv[2]
    if command == "generate":
        generate(current_run)
    elif command == "finish":
        finish(current_run, UUID(sys.argv[3]))
    elif command == "cleanup":
        cleanup(current_run, UUID(sys.argv[3]))
    else:
        raise SystemExit(f"Unknown command: {command}")
