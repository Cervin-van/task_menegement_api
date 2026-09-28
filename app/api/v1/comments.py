from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CommentServiceDep, CurrentUser
from app.schemas.comment import CommentCreate, CommentRead
from app.schemas.common import DbIdPath, Page, PageParams

router = APIRouter(prefix="/tasks/{task_id}/comments", tags=["comments"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def add_comment(
    task_id: DbIdPath, data: CommentCreate, user: CurrentUser, service: CommentServiceDep
) -> CommentRead:
    return CommentRead.model_validate(await service.add(task_id, data, user))


@router.get("")
async def list_comments(
    task_id: DbIdPath,
    params: Annotated[PageParams, Query()],
    _: CurrentUser,
    service: CommentServiceDep,
) -> Page[CommentRead]:
    items, total = await service.list_for_task(task_id, params)
    return Page[CommentRead].build(items, total, params)
