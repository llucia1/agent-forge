import json
import unittest
from unittest.mock import patch
from uuid import UUID

from core.infrastructure.rabbitmq import (
    TASK_QUEUES,
    TaskPublisher,
    UnsupportedAgentError,
)
from core.models.task import Task


class TaskPublisherTests(unittest.TestCase):
    project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")
    task_id = UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b")

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_publishes_persistent_message_to_each_agent_queue(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        channel = connection.channel.return_value
        publisher = TaskPublisher()

        for agent, queue in TASK_QUEUES.items():
            with self.subTest(agent=agent):
                connection.reset_mock()
                channel.reset_mock()
                task = Task(
                    id=self.task_id,
                    project_id=self.project_id,
                    title="Implement feature",
                    description="Publish the task",
                    agent=agent,
                )

                publisher.publish(task)

                channel.queue_declare.assert_called_once_with(
                    queue=queue,
                    durable=True,
                )
                channel.basic_publish.assert_called_once()
                publish_arguments = channel.basic_publish.call_args.kwargs
                self.assertEqual(publish_arguments["exchange"], "")
                self.assertEqual(publish_arguments["routing_key"], queue)
                self.assertEqual(
                    publish_arguments["properties"].content_type,
                    "application/json",
                )
                self.assertEqual(
                    publish_arguments["properties"].delivery_mode,
                    2,
                )
                self.assertEqual(
                    json.loads(publish_arguments["body"]),
                    {
                        "id": str(self.task_id),
                        "project_id": str(self.project_id),
                        "title": "Implement feature",
                        "description": "Publish the task",
                        "agent": agent,
                        "status": "pending",
                    },
                )
                connection.close.assert_called_once_with()

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_rejects_unknown_agent_without_connecting_or_publishing(
        self,
        connection_factory,
    ):
        task = Task(
            id=self.task_id,
            project_id=self.project_id,
            title="Unsupported task",
            description="This agent has no queue",
            agent="security",
        )

        with self.assertRaisesRegex(
            UnsupportedAgentError,
            "Unsupported task agent: security",
        ):
            TaskPublisher().publish(task)

        connection_factory.assert_not_called()

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_closes_connection_when_publication_fails(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        connection.channel.return_value.basic_publish.side_effect = RuntimeError(
            "broker unavailable"
        )
        task = Task(
            id=self.task_id,
            project_id=self.project_id,
            title="Implement feature",
            description="Publish the task",
            agent="backend",
        )

        with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
            TaskPublisher().publish(task)

        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
