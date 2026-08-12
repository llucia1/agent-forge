import json
import os
from collections.abc import Callable
from uuid import UUID

import pika

from core.models.task import Task


TASK_QUEUES = {
    "architect": "tasks.architect",
    "backend": "tasks.backend",
    "frontend": "tasks.frontend",
    "reviewer": "tasks.reviewer",
    "qa": "tasks.qa",
    "devops": "tasks.devops",
}


class UnsupportedAgentError(ValueError):
    """Raised when a task targets an agent without a configured queue."""


def _get_task_queue(agent: str) -> str:
    queue = TASK_QUEUES.get(agent)

    if queue is None:
        raise UnsupportedAgentError(f"Unsupported task agent: {agent}")

    return queue


def _create_connection() -> pika.BlockingConnection:
    credentials = pika.PlainCredentials(
        os.getenv("RABBITMQ_USER"),
        os.getenv("RABBITMQ_PASSWORD"),
    )

    return pika.BlockingConnection(
        pika.ConnectionParameters(
            host=os.getenv("RABBITMQ_HOST"),
            port=int(os.getenv("RABBITMQ_PORT", "5672")),
            credentials=credentials,
        )
    )


def check_rabbitmq() -> bool:
    connection = _create_connection()

    connection.close()

    return True


class TaskPublisher:
    def publish(self, task: Task) -> None:
        queue = _get_task_queue(task.agent)

        message = json.dumps(
            {
                "id": str(task.id),
                "project_id": str(task.project_id),
                "title": task.title,
                "description": task.description,
                "agent": task.agent,
                "status": task.status,
            }
        ).encode("utf-8")

        connection = _create_connection()

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


class TaskConsumer:
    def consume(
        self,
        agent: str,
        handler: Callable[[Task], None],
    ) -> None:
        queue = _get_task_queue(agent)
        connection = _create_connection()

        try:
            channel = connection.channel()
            channel.queue_declare(queue=queue, durable=True)
            channel.basic_qos(prefetch_count=1)

            def on_message(channel, method, properties, body):
                try:
                    task = self._deserialize_task(body)
                    handler(task)
                except Exception:
                    channel.basic_nack(
                        delivery_tag=method.delivery_tag,
                        requeue=False,
                    )
                else:
                    channel.basic_ack(
                        delivery_tag=method.delivery_tag,
                    )

            channel.basic_consume(
                queue=queue,
                on_message_callback=on_message,
                auto_ack=False,
            )
            channel.start_consuming()
        finally:
            if connection.is_open:
                connection.close()

    @staticmethod
    def _deserialize_task(body: bytes) -> Task:
        message = json.loads(body)

        return Task(
            id=UUID(message["id"]),
            project_id=UUID(message["project_id"]),
            title=message["title"],
            description=message["description"],
            agent=message["agent"],
            status=message["status"],
        )
