"""Notification delivery for domain events.

Runs in FastAPI BackgroundTasks after the response is sent. Delivery is a log record for now;
swapping it for email / a message queue doesn't touch services or routers.
"""

import logging
from collections.abc import Sequence

from app.domain.events import DomainEvent, TaskAssigned, TaskStatusChanged

logger = logging.getLogger("app.notifications")


async def dispatch_events(events: Sequence[DomainEvent]) -> None:
    for event in events:
        try:
            _deliver(event)
        except Exception:  # one failed notification must not drop the rest
            logger.exception("Failed to deliver notification: %r", event)


def _deliver(event: DomainEvent) -> None:
    match event:
        case TaskAssigned():
            logger.info(
                "task.assigned task_id=%s to=%s title=%r",
                event.task_id,
                event.assignee_email,
                event.title,
            )
        case TaskStatusChanged():
            for recipient in event.recipients:
                logger.info(
                    "task.status_changed task_id=%s to=%s %s->%s title=%r",
                    event.task_id,
                    recipient,
                    event.old_status,
                    event.new_status,
                    event.title,
                )
