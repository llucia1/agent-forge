import unittest
from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parents[1] / "scripts" / "init_db.sql"


class TaskResultSchemaTests(unittest.TestCase):
    def test_task_results_support_multiple_results_per_task(self):
        schema = " ".join(SCHEMA_PATH.read_text(encoding="utf-8").split())

        self.assertIn("CREATE TABLE IF NOT EXISTS task_results", schema)
        self.assertIn("id UUID PRIMARY KEY", schema)
        self.assertIn(
            "task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE",
            schema,
        )
        self.assertIn(
            "CREATE INDEX IF NOT EXISTS idx_task_results_task_id "
            "ON task_results(task_id)",
            schema,
        )
        self.assertNotIn("UNIQUE(task_id)", schema)
