import json
import logging
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from uuid import UUID

import pika

from core.contracts.configuration import RabbitMQSettings
from core.contracts.messaging import TaskConsumer, TaskHandler, TaskPublisher
from core.models.task import AgentRole, Task, TaskStatus


LOGGER = logging.getLogger(__name__)


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
        executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="agentforge-task",
        )

        try:
            channel = connection.channel()
            channel.basic_qos(prefetch_count=1)

            for queue, handler in routed_handlers:
                channel.queue_declare(queue=queue, durable=True)
                channel.basic_consume(
                    queue=queue,
                    on_message_callback=self._message_callback(
                        connection,
                        executor,
                        handler,
                    ),
                    auto_ack=False,
                )
            channel.start_consuming()
        finally:
            executor.shutdown(wait=True)
            if connection.is_open:
                connection.close()

    def _message_callback(
        self,
        connection,
        executor: ThreadPoolExecutor,
        handler: TaskHandler,
    ):
        def on_message(channel, method, properties, body):
            try:
                task = self._deserialize_task(body)
            except Exception:
                LOGGER.exception(
                    "RabbitMQ delivery could not be deserialized "
                    "task_id=unknown delivery_tag=%s redelivered=%s",
                    method.delivery_tag,
                    method.redelivered,
                )
                self._schedule_settlement(
                    connection=connection,
                    channel=channel,
                    succeeded=False,
                    task_id="unknown",
                    delivery_tag=method.delivery_tag,
                    redelivered=method.redelivered,
                )
                return

            LOGGER.info(
                "RabbitMQ delivery received task_id=%s delivery_tag=%s "
                "redelivered=%s",
                task.id,
                method.delivery_tag,
                method.redelivered,
            )
            try:
                executor.submit(
                    self._handle_delivery,
                    connection,
                    channel,
                    method.delivery_tag,
                    method.redelivered,
                    handler,
                    task,
                )
            except RuntimeError:
                LOGGER.exception(
                    "RabbitMQ worker unavailable task_id=%s "
                    "delivery_tag=%s redelivered=%s",
                    task.id,
                    method.delivery_tag,
                    method.redelivered,
                )
                self._schedule_settlement(
                    connection=connection,
                    channel=channel,
                    succeeded=False,
                    task_id=str(task.id),
                    delivery_tag=method.delivery_tag,
                    redelivered=method.redelivered,
                )

        return on_message

    def _handle_delivery(
        self,
        connection,
        channel,
        delivery_tag: int,
        redelivered: bool,
        handler: TaskHandler,
        task: Task,
    ) -> None:
        succeeded = False
        try:
            handler.handle(task)
        except Exception:
            LOGGER.exception(
                "RabbitMQ task handler failed task_id=%s delivery_tag=%s "
                "redelivered=%s",
                task.id,
                delivery_tag,
                redelivered,
            )
        else:
            succeeded = True

        self._schedule_settlement(
            connection=connection,
            channel=channel,
            succeeded=succeeded,
            task_id=str(task.id),
            delivery_tag=delivery_tag,
            redelivered=redelivered,
        )

    @staticmethod
    def _schedule_settlement(
        connection,
        channel,
        succeeded: bool,
        task_id: str,
        delivery_tag: int,
        redelivered: bool,
    ) -> None:
        action = "ACK" if succeeded else "NACK"
        callback = partial(
            RabbitMQTaskConsumer._settle,
            channel,
            succeeded,
            task_id,
            delivery_tag,
            redelivered,
        )
        try:
            connection.add_callback_threadsafe(callback)
        except Exception:
            LOGGER.exception(
                "RabbitMQ connection unavailable; could not schedule %s "
                "task_id=%s delivery_tag=%s redelivered=%s",
                action,
                task_id,
                delivery_tag,
                redelivered,
            )

    @staticmethod
    def _settle(
        channel,
        succeeded: bool,
        task_id: str,
        delivery_tag: int,
        redelivered: bool,
    ) -> None:
        action = "ACK" if succeeded else "NACK"
        if not channel.is_open:
            LOGGER.error(
                "RabbitMQ connection unavailable; could not send %s "
                "task_id=%s delivery_tag=%s redelivered=%s",
                action,
                task_id,
                delivery_tag,
                redelivered,
            )
            return
        try:
            if succeeded:
                channel.basic_ack(delivery_tag=delivery_tag)
            else:
                channel.basic_nack(
                    delivery_tag=delivery_tag,
                    requeue=False,
                )
        except Exception:
            LOGGER.exception(
                "RabbitMQ connection unavailable; could not send %s "
                "task_id=%s delivery_tag=%s redelivered=%s",
                action,
                task_id,
                delivery_tag,
                redelivered,
            )
            raise

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
