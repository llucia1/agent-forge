import unittest
from unittest.mock import call, patch
from uuid import UUID

from core.contracts.configuration import DatabaseSettings
from core.contracts.repositories import ProjectCreator, ProjectReader
from core.infrastructure.repositories.project_repository import (
    PostgresProjectRepository,
)
from core.models.project import Project


class ProjectRepositoryTests(unittest.TestCase):
    settings = DatabaseSettings(
        host="postgres",
        port="5432",
        database="agent_forge",
        user="agent_forge",
        password="database-key",
    )

    @patch(
        "core.infrastructure.repositories.project_repository.Jsonb",
        side_effect=lambda value: value,
    )
    @patch("core.infrastructure.repositories.project_repository.psycopg.connect")
    def test_create_persists_complete_technical_definition(
        self,
        connect,
        jsonb,
    ):
        project = Project(
            name="AgentForge",
            description="Agent platform",
            backend_stack={"language": "Python"},
            backend_architecture={"style": "layered"},
            frontend_stack={"framework": "React"},
            frontend_architecture={"pattern": "component-based"},
            infrastructure={"runtime": "Docker Compose"},
            technical_constraints=["Use Python 3.12"],
        )
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value

        repository = PostgresProjectRepository(self.settings)
        self.assertIsInstance(repository, ProjectCreator)
        self.assertIsInstance(repository, ProjectReader)
        repository.create(project)

        statement, parameters = cursor.execute.call_args.args
        normalized_statement = " ".join(statement.split())
        for column in (
            "backend_stack",
            "backend_architecture",
            "frontend_stack",
            "frontend_architecture",
            "infrastructure",
            "technical_constraints",
        ):
            self.assertIn(column, normalized_statement)
        self.assertEqual(
            parameters,
            (
                project.id,
                project.name,
                project.description,
                project.status,
                project.backend_stack,
                project.backend_architecture,
                project.frontend_stack,
                project.frontend_architecture,
                project.infrastructure,
                project.technical_constraints,
            ),
        )
        self.assertEqual(
            jsonb.call_args_list,
            [
                call(project.backend_stack),
                call(project.backend_architecture),
                call(project.frontend_stack),
                call(project.frontend_architecture),
                call(project.infrastructure),
                call(project.technical_constraints),
            ],
        )

    @patch("core.infrastructure.repositories.project_repository.psycopg.connect")
    def test_find_by_id_returns_complete_project(self, connect):
        project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = (
            project_id,
            "AgentForge",
            "Agent platform",
            "draft",
            {"language": "Python"},
            {"style": "layered"},
            {"framework": "React"},
            {"pattern": "component-based"},
            {"runtime": "Docker Compose"},
            ["Use Python 3.12"],
        )

        project = PostgresProjectRepository(self.settings).find_by_id(
            project_id
        )

        statement, parameters = cursor.execute.call_args.args
        normalized_statement = " ".join(statement.split())
        self.assertIn("FROM projects WHERE id = %s", normalized_statement)
        self.assertEqual(parameters, (project_id,))
        self.assertIsNotNone(project)
        self.assertEqual(project.id, project_id)
        self.assertEqual(project.name, "AgentForge")
        self.assertEqual(project.description, "Agent platform")
        self.assertEqual(project.status, "draft")
        self.assertEqual(project.backend_stack, {"language": "Python"})
        self.assertEqual(project.backend_architecture, {"style": "layered"})
        self.assertEqual(project.frontend_stack, {"framework": "React"})
        self.assertEqual(
            project.frontend_architecture,
            {"pattern": "component-based"},
        )
        self.assertEqual(
            project.infrastructure,
            {"runtime": "Docker Compose"},
        )
        self.assertEqual(
            project.technical_constraints,
            ["Use Python 3.12"],
        )

    @patch("core.infrastructure.repositories.project_repository.psycopg.connect")
    def test_find_by_id_returns_none_when_project_does_not_exist(self, connect):
        project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")
        connection = connect.return_value.__enter__.return_value
        cursor = connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = None

        project = PostgresProjectRepository(self.settings).find_by_id(
            project_id
        )

        self.assertIsNone(project)


if __name__ == "__main__":
    unittest.main()
