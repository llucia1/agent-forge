import ast
import unittest
from pathlib import Path


MAIN_PATH = Path(__file__).resolve().parents[1] / "main.py"


class MainCompositionTests(unittest.TestCase):
    def test_composes_frontend_agent_and_routes_frontend_tasks(self):
        tree = ast.parse(
            MAIN_PATH.read_text(encoding="utf-8"),
            filename=str(MAIN_PATH),
        )

        frontend_assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "frontend_agent"
                for target in node.targets
            )
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "FrontendAgent"
        ]
        self.assertEqual(len(frontend_assignments), 1)

        consume_mappings = [
            argument
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "consume"
            for argument in node.args
            if isinstance(argument, ast.Dict)
        ]
        self.assertEqual(len(consume_mappings), 1)

        mapping = consume_mappings[0]
        routes_frontend = any(
            isinstance(key, ast.Attribute)
            and isinstance(key.value, ast.Name)
            and key.value.id == "AgentRole"
            and key.attr == "FRONTEND"
            and isinstance(value, ast.Name)
            and value.id == "frontend_agent"
            for key, value in zip(mapping.keys, mapping.values)
        )
        self.assertTrue(routes_frontend)

    def test_composes_reviewer_with_reader_and_routes_reviewer_tasks(self):
        tree = ast.parse(
            MAIN_PATH.read_text(encoding="utf-8"),
            filename=str(MAIN_PATH),
        )

        reviewer_assignments = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "reviewer_agent"
                for target in node.targets
            )
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "ReviewerAgent"
        ]
        self.assertEqual(len(reviewer_assignments), 1)
        keyword_names = {
            keyword.arg for keyword in reviewer_assignments[0].value.keywords
        }
        self.assertIn("workspace_reader", keyword_names)
        self.assertNotIn("workspace", keyword_names)

        consume_mappings = [
            argument
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "consume"
            for argument in node.args
            if isinstance(argument, ast.Dict)
        ]
        self.assertEqual(len(consume_mappings), 1)

        mapping = consume_mappings[0]
        routes_reviewer = any(
            isinstance(key, ast.Attribute)
            and isinstance(key.value, ast.Name)
            and key.value.id == "AgentRole"
            and key.attr == "REVIEWER"
            and isinstance(value, ast.Name)
            and value.id == "reviewer_agent"
            for key, value in zip(mapping.keys, mapping.values)
        )
        self.assertTrue(routes_reviewer)


if __name__ == "__main__":
    unittest.main()
