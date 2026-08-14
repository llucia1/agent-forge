import json
from collections.abc import Mapping
from uuid import UUID

import pika

from core.contracts.configuration import RabbitMQSettings
from core.contracts.messaging import TaskConsumer, TaskHandler, TaskPublisher
from core.models.task import AgentRole, Task, TaskStatus


class UnsupportedAgentError(ValueError):
    """Raised when a task targets an agent without a configured queue."""


def _get_task_queue(settings: RabbitMQSettings, agent: AgentRole) -> str:
    queue = settings.task_queues.get(agent)

    if queue is None:
        raise UnsupportedAgentError(f"Unsupported task agent: {agent}")

    return queue


def _create_connection(settings: RabbitMQSettings) -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(
        settings.user,
        settings.password,
    )

    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=settings.host,
            port=settings.port,
            credentials=credentials,
        )
    )


def check_rabbitmq(settings: RabbitMQSettings) -> bool:
    connection = _create_connection(settings)

    connection.close()

    return True


class RabbitMQTaskPublisher(TaskPublisher):
    def __init__(self, settings: RabbitMQSettings):
        self.settings = settings

    def publish(self, task: Task) -> None:
        queue = _get_task_queue(self.settings, task.agent)

        message = json.dumps(
            {
                "id": str(task.id),
                "project_id": str(task.project_id),
                "title": task.title,
                "description": task.description,
                "agent": str(task.agent),
                "status": str(task.status),
            }
        ).encode("utf-8")

        connection = _create_connection(self.settings)

        try:
            channel = connection.channel()
            channel.queue_declare(queue=queue, durable=True)
            channel.basic_publish(
                exchange="",
                routing_key=queue,
                body=message,
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=2,
                ),
            )
        finally:
            connection.close()


class RabbitMQTaskConsumer(TaskConsumer):
    def __init__(self, settings: RabbitMQSettings):
        self.settings = settings

    def consume(
        self,
        handlers: Mapping[AgentRole, TaskHandler],
    ) -> None:
        routed_handlers = [
            (_get_task_queue(self.settings, agent), handler)
            for agent, handler in handlers.items()
        ]
        connection = _create_connection(self.settings)

        try:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=1)

            for queue, handler in routed_handlers:
                channel.queue_declare(queue=queue, durable=True)
                channel.basic_consume(
                    queue=queue,
                    on_message_callback=self._message_callback(handler),
                    auto_ack=False,
                )
            channel.start_consuming()
        finally:
            if connection.is_open:
                connection.close()

    def _message_callback(self, handler: TaskHandler):
        def on_message(channel, method, properties, body):
            try:
                task = self._deserialize_task(body)
                handler.handle(task)
            except Exception:
                channel.basic_nack(
                    delivery_tag=method.delivery_tag,
                    requeue=False,
                )
            else:
                channel.basic_ack(
                    delivery_tag=method.delivery_tag,
                )

        return on_message

    @staticmethod
    def _deserialize_task(body: bytes) -> Task:
        message = json.loads(body)

        return Task(
            id=UUID(message["id"]),
            project_id=UUID(message["project_id"]),
            title=message["title"],
            description=message["description"],
            agent=AgentRole(message["agent"]),
            status=TaskStatus(message["status"]),
        )
