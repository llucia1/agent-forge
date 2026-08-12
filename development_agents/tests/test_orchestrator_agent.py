import unittest
from unittest.mock import Mock

from agents.orchestrator.agent import OrchestratorAgent
from core.contracts.orchestration import OrchestrationUseCases
from core.models.project import Project
from core.models.task import AgentRole, Task


class OrchestratorAgentTests(unittest.TestCase):
    def setUp(self):
        self.orchestration = Mock(spec=OrchestrationUseCases)
        self.agent = OrchestratorAgent(self.orchestration)

    def test_delegates_project_creation_to_orchestration_contract(self):
        expected = Project(name="AgentForge", description="Agent platform")
        self.orchestration.create_project.return_value = expected

        result = self.agent.create_project(
            name="AgentForge",
            description="Agent platform",
        )

        self.assertIs(result, expected)
        self.orchestration.create_project.assert_called_once_with(
            name="AgentForge",
            description="Agent platform",
            backend_stack=None,
            backend_architecture=None,
            frontend_stack=None,
            frontend_architecture=None,
            infrastructure=None,
            technical_constraints=None,
        )

    def test_delegates_task_creation_to_orchestration_contract(self):
        project = Project(name="AgentForge", description="Agent platform")
        expected = Task(
            project_id=project.id,
            title="Define architecture",
            description="Define the application architecture",
            agent=AgentRole.ARCHITECT,
        )
        self.orchestration.create_task.return_value = expected

        result = self.agent.create_task(
            project=project,
            title="Define architecture",
            description="Define the application architecture",
            agent=AgentRole.ARCHITECT,
        )

        self.assertIs(result, expected)
        self.orchestration.create_task.assert_called_once_with(
            project=project,
            title="Define architecture",
            description="Define the application architecture",
            agent=AgentRole.ARCHITECT,
        )


if __name__ == "__main__":
    unittest.main()
