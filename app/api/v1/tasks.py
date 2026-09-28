from fastapi import APIRouter, Response, status

from app.api.deps import CurrentUser, TaskServiceDep
from app.schemas.task import TaskCreate, TaskRead, TaskUpdate

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_task(data: TaskCreate, user: CurrentUser, service: TaskServiceDep) -> TaskRead:
    return TaskRead.model_validate(await service.create(data, user))


@router.get("/{task_id}")
async def get_task(task_id: int, _: CurrentUser, service: TaskServiceDep) -> TaskRead:
    return TaskRead.model_validate(await service.get(task_id))


@router.patch("/{task_id}")
async def update_task(
    task_id: int, data: TaskUpdate, user: CurrentUser, service: TaskServiceDep
) -> TaskRead:
    return TaskRead.model_validate(await service.update(task_id, data, user))


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: int, user: CurrentUser, service: TaskServiceDep) -> Response:
    await service.delete(task_id, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
