import unittest
from uuid import UUID

from core.memory import DefaultContextProvider
from core.models.project import Project
from core.models.task import Task


class DefaultContextProviderTests(unittest.TestCase):
    def test_build_returns_empty_agentforge_owned_context(self):
        project = Project(
            id=UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b"),
            name="AgentForge",
            description="Agent platform",
        )
        task = Task(
            id=UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b"),
            project_id=project.id,
            title="Define architecture",
            description="Define the application architecture",
            agent="architect",
        )

        context = DefaultContextProvider().build(task, project)

        self.assertEqual(
            context,
            {
                "project_memory": {},
                "previous_decisions": [],
                "feedback": [],
                "rag_context": None,
            },
        )

    def test_build_returns_fresh_mutable_collections(self):
        project = Project(name="AgentForge", description="Agent platform")
        task = Task(
            project_id=project.id,
            title="Define architecture",
            description="Define the application architecture",
            agent="architect",
        )
        provider = DefaultContextProvider()

        first = provider.build(task, project)
        second = provider.build(task, project)
        first["project_memory"]["summary"] = "Existing knowledge"
        first["previous_decisions"].append("Use PostgreSQL")

        self.assertEqual(second["project_memory"], {})
        self.assertEqual(second["previous_decisions"], [])


if __name__ == "__main__":
    unittest.main()
