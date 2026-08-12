import unittest

from core.models.project import Project


class ProjectTests(unittest.TestCase):
    def test_technical_definition_defaults_are_independent(self):
        first = Project(name="First", description="First project")
        second = Project(name="Second", description="Second project")

        first.backend_stack["language"] = "Python"
        first.technical_constraints.append("Must run in containers")

        self.assertEqual(second.backend_stack, {})
        self.assertEqual(second.technical_constraints, [])

    def test_accepts_complete_technical_definition(self):
        project = Project(
            name="AgentForge",
            description="Agent platform",
            backend_stack={"language": "Python", "database": "PostgreSQL"},
            backend_architecture={"style": "layered"},
            frontend_stack={"framework": "React"},
            frontend_architecture={"pattern": "component-based"},
            infrastructure={"runtime": "Docker Compose"},
            technical_constraints=[
                "Use Python 3.12",
                "Keep domain infrastructure-agnostic",
            ],
        )

        self.assertEqual(project.backend_stack["language"], "Python")
        self.assertEqual(project.backend_architecture["style"], "layered")
        self.assertEqual(project.frontend_stack["framework"], "React")
        self.assertEqual(
            project.frontend_architecture["pattern"],
            "component-based",
        )
        self.assertEqual(project.infrastructure["runtime"], "Docker Compose")
        self.assertEqual(len(project.technical_constraints), 2)


if __name__ == "__main__":
    unittest.main()
