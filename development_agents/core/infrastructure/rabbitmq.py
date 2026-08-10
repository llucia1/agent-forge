import json
import os

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
        queue = TASK_QUEUES.get(task.agent)

        if queue is None:
            raise UnsupportedAgentError(
                f"Unsupported task agent: {task.agent}"
            )

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
