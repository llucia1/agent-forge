import json
import queue
import threading
import unittest
from unittest.mock import ANY, Mock, call, patch
from uuid import UUID

from core.contracts.configuration import RabbitMQSettings
from core.contracts.messaging import TaskConsumer, TaskHandler, TaskPublisher
from core.infrastructure.rabbitmq import (
    RabbitMQTaskConsumer,
    RabbitMQTaskPublisher,
    UnsupportedAgentError,
)
from core.models.task import AgentRole, Task


TASK_QUEUES = {
    AgentRole.ARCHITECT: "tasks.architect",
    AgentRole.BACKEND: "tasks.backend",
    AgentRole.FRONTEND: "tasks.frontend",
    AgentRole.REVIEWER: "tasks.reviewer",
    AgentRole.QA: "tasks.qa",
    AgentRole.DEVOPS: "tasks.devops",
}


def rabbitmq_settings() -> RabbitMQSettings:
    return RabbitMQSettings(
        host="rabbitmq",
        port=5672,
        user="agent_forge",
        password="broker-key",
        task_queues=TASK_QUEUES,
    )


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
        publisher = RabbitMQTaskPublisher(rabbitmq_settings())
        self.assertIsInstance(publisher, TaskPublisher)

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
            RabbitMQTaskPublisher(rabbitmq_settings()).publish(task)

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
            agent=AgentRole.BACKEND,
        )

        with self.assertRaisesRegex(RuntimeError, "broker unavailable"):
            RabbitMQTaskPublisher(rabbitmq_settings()).publish(task)

        connection.close.assert_called_once_with()


class TaskConsumerTests(unittest.TestCase):
    task_id = UUID("2cb5fe26-74a0-48f1-a989-aaf9b41b343b")
    project_id = UUID("93de5ea5-729a-4c5e-8dc3-443165ed516b")

    def _message(self, agent=AgentRole.ARCHITECT):
        return json.dumps(
            {
                "id": str(self.task_id),
                "project_id": str(self.project_id),
                "title": "Define architecture",
                "description": "Define the application architecture",
                "agent": str(agent),
                "status": "pending",
            }
        ).encode("utf-8")

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_consumes_deserialized_task_and_acknowledges_after_success(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        connection.is_open = True
        channel = connection.channel.return_value
        channel.is_open = True
        method = Mock(delivery_tag=42, redelivered=False)
        events = []
        handled_tasks = []
        connection.add_callback_threadsafe.side_effect = (
            lambda callback: callback()
        )

        handler = Mock(spec=TaskHandler)

        def handle(task):
            events.append("handled")
            handled_tasks.append(task)

        handler.handle.side_effect = handle

        channel.basic_ack.side_effect = lambda **kwargs: events.append("ack")

        def start_consuming():
            callback = channel.basic_consume.call_args.kwargs[
                "on_message_callback"
            ]
            callback(channel, method, None, self._message())

        channel.start_consuming.side_effect = start_consuming

        consumer = RabbitMQTaskConsumer(rabbitmq_settings())
        self.assertIsInstance(consumer, TaskConsumer)
        consumer.consume({AgentRole.ARCHITECT: handler})

        self.assertEqual(events, ["handled", "ack"])
        self.assertEqual(len(handled_tasks), 1)
        task = handled_tasks[0]
        self.assertIsInstance(task, Task)
        self.assertEqual(task.id, self.task_id)
        self.assertEqual(task.project_id, self.project_id)
        self.assertEqual(task.title, "Define architecture")
        self.assertEqual(task.agent, "architect")
        self.assertEqual(task.status, "pending")
        channel.queue_declare.assert_called_once_with(
            queue="tasks.architect",
            durable=True,
        )
        channel.basic_qos.assert_called_once_with(prefetch_count=1)
        channel.basic_consume.assert_called_once_with(
            queue="tasks.architect",
            on_message_callback=ANY,
            auto_ack=False,
        )
        channel.basic_ack.assert_called_once_with(delivery_tag=42)
        channel.basic_nack.assert_not_called()
        connection.add_callback_threadsafe.assert_called_once()
        connection.close.assert_called_once_with()

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_nacks_without_requeue_when_handler_fails(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        connection.is_open = True
        channel = connection.channel.return_value
        channel.is_open = True
        method = Mock(delivery_tag=84, redelivered=False)
        events = []
        connection.add_callback_threadsafe.side_effect = (
            lambda callback: callback()
        )

        handler = Mock(spec=TaskHandler)

        def handle(task):
            events.append("handled")
            raise RuntimeError("processing failed")

        handler.handle.side_effect = handle

        channel.basic_nack.side_effect = (
            lambda **kwargs: events.append("nack")
        )

        def start_consuming():
            callback = channel.basic_consume.call_args.kwargs[
                "on_message_callback"
            ]
            callback(channel, method, None, self._message())

        channel.start_consuming.side_effect = start_consuming

        with self.assertLogs(
            "core.infrastructure.rabbitmq",
            level="ERROR",
        ):
            RabbitMQTaskConsumer(rabbitmq_settings()).consume(
                {AgentRole.ARCHITECT: handler},
            )

        self.assertEqual(events, ["handled", "nack"])
        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_called_once_with(
            delivery_tag=84,
            requeue=False,
        )
        connection.add_callback_threadsafe.assert_called_once()
        connection.close.assert_called_once_with()

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_routes_architect_and_backend_queues_to_their_handlers(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        connection.is_open = True
        channel = connection.channel.return_value
        channel.is_open = True
        architect_handler = Mock(spec=TaskHandler)
        backend_handler = Mock(spec=TaskHandler)

        def start_consuming():
            callbacks_by_queue = {
                call.kwargs["queue"]: call.kwargs["on_message_callback"]
                for call in channel.basic_consume.call_args_list
            }
            callbacks_by_queue["tasks.architect"](
                channel,
                Mock(delivery_tag=1, redelivered=False),
                None,
                self._message(AgentRole.ARCHITECT),
            )
            callbacks_by_queue["tasks.backend"](
                channel,
                Mock(delivery_tag=2, redelivered=False),
                None,
                self._message(AgentRole.BACKEND),
            )

        channel.start_consuming.side_effect = start_consuming

        RabbitMQTaskConsumer(rabbitmq_settings()).consume(
            {
                AgentRole.ARCHITECT: architect_handler,
                AgentRole.BACKEND: backend_handler,
            }
        )

        self.assertEqual(
            channel.queue_declare.call_args_list,
            [
                call(
                    queue="tasks.architect",
                    durable=True,
                ),
                call(
                    queue="tasks.backend",
                    durable=True,
                ),
            ],
        )
        self.assertEqual(architect_handler.handle.call_count, 1)
        self.assertEqual(backend_handler.handle.call_count, 1)
        self.assertEqual(
            architect_handler.handle.call_args.args[0].agent,
            AgentRole.ARCHITECT,
        )
        self.assertEqual(
            backend_handler.handle.call_args.args[0].agent,
            AgentRole.BACKEND,
        )

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_long_handler_does_not_block_io_and_ack_runs_on_io_thread(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        connection.is_open = True
        channel = connection.channel.return_value
        channel.is_open = True
        method = Mock(delivery_tag=126, redelivered=True)
        handler = Mock(spec=TaskHandler)
        handler_started = threading.Event()
        release_handler = threading.Event()
        settlements = queue.Queue()
        handler_thread_ids = []
        ack_thread_ids = []
        io_thread_ids = []

        def handle(task):
            handler_thread_ids.append(threading.get_ident())
            handler_started.set()
            self.assertTrue(release_handler.wait(timeout=2))

        handler.handle.side_effect = handle
        connection.add_callback_threadsafe.side_effect = settlements.put
        channel.basic_ack.side_effect = (
            lambda **kwargs: ack_thread_ids.append(threading.get_ident())
        )

        def start_consuming():
            io_thread_ids.append(threading.get_ident())
            callback = channel.basic_consume.call_args.kwargs[
                "on_message_callback"
            ]
            callback(channel, method, None, self._message())

            self.assertTrue(handler_started.wait(timeout=2))
            self.assertFalse(release_handler.is_set())
            release_handler.set()
            settlement = settlements.get(timeout=2)
            settlement()

        channel.start_consuming.side_effect = start_consuming

        with self.assertLogs(
            "core.infrastructure.rabbitmq",
            level="INFO",
        ) as logs:
            RabbitMQTaskConsumer(rabbitmq_settings()).consume(
                {AgentRole.ARCHITECT: handler},
            )

        self.assertNotEqual(handler_thread_ids, io_thread_ids)
        self.assertEqual(ack_thread_ids, io_thread_ids)
        channel.basic_ack.assert_called_once_with(delivery_tag=126)
        self.assertTrue(
            any(
                str(self.task_id) in message
                and "delivery_tag=126" in message
                and "redelivered=True" in message
                for message in logs.output
            )
        )

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_single_worker_processes_handlers_sequentially(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        connection.is_open = True
        channel = connection.channel.return_value
        channel.is_open = True
        first_handler = Mock(spec=TaskHandler)
        second_handler = Mock(spec=TaskHandler)
        first_started = threading.Event()
        release_first = threading.Event()
        second_started = threading.Event()
        settlements = queue.Queue()

        def handle_first(task):
            first_started.set()
            self.assertTrue(release_first.wait(timeout=2))

        first_handler.handle.side_effect = handle_first
        second_handler.handle.side_effect = (
            lambda task: second_started.set()
        )
        connection.add_callback_threadsafe.side_effect = settlements.put

        def start_consuming():
            callbacks_by_queue = {
                call.kwargs["queue"]: call.kwargs["on_message_callback"]
                for call in channel.basic_consume.call_args_list
            }
            callbacks_by_queue["tasks.architect"](
                channel,
                Mock(delivery_tag=1, redelivered=False),
                None,
                self._message(AgentRole.ARCHITECT),
            )
            callbacks_by_queue["tasks.backend"](
                channel,
                Mock(delivery_tag=2, redelivered=False),
                None,
                self._message(AgentRole.BACKEND),
            )

            self.assertTrue(first_started.wait(timeout=2))
            self.assertFalse(second_started.is_set())
            release_first.set()
            settlements.get(timeout=2)()
            self.assertTrue(second_started.wait(timeout=2))
            settlements.get(timeout=2)()

        channel.start_consuming.side_effect = start_consuming

        RabbitMQTaskConsumer(rabbitmq_settings()).consume(
            {
                AgentRole.ARCHITECT: first_handler,
                AgentRole.BACKEND: second_handler,
            }
        )

        self.assertEqual(first_handler.handle.call_count, 1)
        self.assertEqual(second_handler.handle.call_count, 1)
        self.assertEqual(channel.basic_ack.call_count, 2)

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_logs_when_ack_cannot_be_scheduled_on_closed_connection(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        connection.is_open = False
        connection.add_callback_threadsafe.side_effect = RuntimeError(
            "connection closed"
        )
        channel = connection.channel.return_value
        channel.is_open = False
        handler = Mock(spec=TaskHandler)

        def start_consuming():
            callback = channel.basic_consume.call_args.kwargs[
                "on_message_callback"
            ]
            callback(
                channel,
                Mock(delivery_tag=168, redelivered=True),
                None,
                self._message(),
            )

        channel.start_consuming.side_effect = start_consuming

        with self.assertLogs(
            "core.infrastructure.rabbitmq",
            level="ERROR",
        ) as logs:
            RabbitMQTaskConsumer(rabbitmq_settings()).consume(
                {AgentRole.ARCHITECT: handler},
            )

        self.assertTrue(
            any(
                "could not schedule ACK" in message
                and str(self.task_id) in message
                and "delivery_tag=168" in message
                and "redelivered=True" in message
                for message in logs.output
            )
        )
        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_not_called()

    @patch("core.infrastructure.rabbitmq.pika.BlockingConnection")
    def test_logs_when_nack_cannot_be_scheduled_on_closed_connection(
        self,
        connection_factory,
    ):
        connection = connection_factory.return_value
        connection.is_open = False
        connection.add_callback_threadsafe.side_effect = RuntimeError(
            "connection closed"
        )
        channel = connection.channel.return_value
        channel.is_open = False
        handler = Mock(spec=TaskHandler)
        handler.handle.side_effect = RuntimeError("processing failed")

        def start_consuming():
            callback = channel.basic_consume.call_args.kwargs[
                "on_message_callback"
            ]
            callback(
                channel,
                Mock(delivery_tag=210, redelivered=True),
                None,
                self._message(),
            )

        channel.start_consuming.side_effect = start_consuming

        with self.assertLogs(
            "core.infrastructure.rabbitmq",
            level="ERROR",
        ) as logs:
            RabbitMQTaskConsumer(rabbitmq_settings()).consume(
                {AgentRole.ARCHITECT: handler},
            )

        self.assertTrue(
            any(
                "could not schedule NACK" in message
                and str(self.task_id) in message
                and "delivery_tag=210" in message
                and "redelivered=True" in message
                for message in logs.output
            )
        )
        channel.basic_ack.assert_not_called()
        channel.basic_nack.assert_not_called()


if __name__ == "__main__":
    unittest.main()
