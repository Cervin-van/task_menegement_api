from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Response, status

from app.api.deps import CurrentUser, TaskServiceDep
from app.notifications import dispatch_events
from app.schemas.common import DbIdPath, Page, PageParams
from app.schemas.task import (
    TaskCreate,
    TaskListQuery,
    TaskRead,
    TaskStats,
    TaskStatusUpdate,
    TaskUpdate,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(
    data: TaskCreate, user: CurrentUser, service: TaskServiceDep, background: BackgroundTasks
) -> TaskRead:
    task = await service.create(data, user)
    background.add_task(dispatch_events, service.pop_events())
    return TaskRead.model_validate(task)


@router.get("")
async def list_tasks(
    query: Annotated[TaskListQuery, Query()], _: CurrentUser, service: TaskServiceDep
) -> Page[TaskRead]:
    items, total = await service.list_tasks(query)
    return Page[TaskRead].build(items, total, query)


# Static paths must be declared before /{task_id}.
@router.get("/overdue")
async def list_overdue_tasks(
    params: Annotated[PageParams, Query()], _: CurrentUser, service: TaskServiceDep
) -> Page[TaskRead]:
    """Deadline passed and status is not done/cancelled; most overdue first."""
    items, total = await service.list_overdue(params)
    return Page[TaskRead].build(items, total, params)


@router.get("/stats")
async def task_stats(_: CurrentUser, service: TaskServiceDep) -> TaskStats:
    return await service.stats()


@router.get("/{task_id}")
async def get_task(task_id: DbIdPath, _: CurrentUser, service: TaskServiceDep) -> TaskRead:
    return TaskRead.model_validate(await service.get(task_id))


@router.patch("/{task_id}")
async def update_task(
    task_id: DbIdPath,
    data: TaskUpdate,
    user: CurrentUser,
    service: TaskServiceDep,
    background: BackgroundTasks,
) -> TaskRead:
    task = await service.update(task_id, data, user)
    background.add_task(dispatch_events, service.pop_events())
    return TaskRead.model_validate(task)


@router.patch("/{task_id}/status")
async def change_task_status(
    task_id: DbIdPath,
    data: TaskStatusUpdate,
    user: CurrentUser,
    service: TaskServiceDep,
    background: BackgroundTasks,
) -> TaskRead:
    task = await service.change_status(task_id, data.status, user)
    background.add_task(dispatch_events, service.pop_events())
    return TaskRead.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: DbIdPath, user: CurrentUser, service: TaskServiceDep) -> Response:
    await service.delete(task_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
