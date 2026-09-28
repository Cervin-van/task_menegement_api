# PLAN — Task Management API

Репозиторій: https://github.com/Cervin-van/task_menegement_api.git

## Узгоджені рішення по неоднозначностях ТЗ
- Статуси: `backlog, todo, in_progress, review, done, cancelled` (Todo додано).
- Ліміт 10 активних задач — для виконавця.
- Права: редагувати/статус — автор або виконавець; видаляти — тільки автор.
- Періодична задача — asyncio-loop у lifespan + advisory lock; BackgroundTasks — нотифікації.

## Архітектура
```
app/
  main.py                 # create_app(), lifespan (старт/стоп overdue-worker), exception handlers
  core/config.py          # Settings (pydantic-settings, .env)
  core/security.py        # hash/verify (argon2), encode/decode JWT (access + refresh)
  core/exceptions.py      # DomainError -> NotFound/Forbidden/Conflict/BusinessRuleViolation(code, message)
  db/session.py           # async engine, async_sessionmaker, get_session dependency
  db/base.py              # DeclarativeBase + naming_convention (стабільні імена constraint-ів для Alembic)
  models/ user.py task.py comment.py
  schemas/ auth.py user.py task.py comment.py common.py (Page[T], ErrorResponse)
  repositories/ user.py task.py comment.py   # тільки запити
  services/ auth.py task.py comment.py stats.py
  domain/status.py        # TaskStatus, TaskPriority, ALLOWED_TRANSITIONS, ACTIVE/FINAL sets
  api/deps.py             # get_current_user, get_task_service, ...
  api/v1/ auth.py users.py tasks.py comments.py
  workers/overdue.py      # periodic loop + cancel_overdue()
  notifications.py        # BackgroundTasks callbacks (structured log)
alembic/ + alembic.ini
tests/ conftest.py test_auth.py test_tasks_crud.py test_status_transitions.py test_filters.py test_overdue_stats.py test_worker.py
Dockerfile, docker-compose.yml, .env.example, pyproject.toml, README.md
```

### Моделі
- `users`: id, email (unique, citext або lower-index), hashed_password, full_name, created_at.
- `tasks`: id, title, description, status (PG ENUM), priority (SmallInteger: 1 low/2 medium/3 high — сортування `DESC` дає High→Medium→Low без CASE), author_id FK, assignee_id FK nullable, deadline timestamptz nullable, created_at (server_default now()), updated_at (onupdate). Індекси: status, assignee_id, deadline, (priority, deadline); GIN trigram на title/description.
- `comments`: id, task_id FK ON DELETE CASCADE, author_id FK, text, created_at.

## Бізнес-правила (services/task.py)
| Правило | Де / як | Код |
|---|---|---|
| Переходи: backlog→todo, todo→in_progress, in_progress→review, review→done; backlog/todo/in_progress→cancelled | `ALLOWED_TRANSITIONS` dict у `domain/status.py`; назад/інше — помилка | 409 |
| Done тільки з assignee і не простроченим дедлайном | `change_status` | 409 |
| deadline ≥ now при create/update | сервіс (не лише Pydantic — тестується з time-machine) | 422 |
| Зміна assignee заборонена в review/done | `update_task` | 409 |
| Done/Cancelled — не редагуються (PATCH і статус) | `update_task`, `change_status` | 409 |
| Не видаляти в in_progress/review | `delete_task` | 409 |
| ≤10 активних у виконавця | при призначенні assignee на активну задачу і при переході backlog→todo; `SELECT ... FOR UPDATE` рядка user-а → без race condition | 409 |
| Статус у PATCH ігнорується/заборонений | окрема схема `TaskUpdate` без status; `PATCH /tasks/{id}/status` | — |
| Автоскасування воркером | системний перехід з будь-якого не фінального статусу (включно з review) — задокументувати в README як свідоме рішення | — |

Помилки — єдиний формат `{"error": {"code": "INVALID_STATUS_TRANSITION", "message": "...", "details": {...}}}`.

## API (`/api/v1`)
- `POST /auth/register`, `POST /auth/login` (OAuth2PasswordRequestForm — працює кнопка Authorize у Swagger), `POST /auth/refresh`, `GET /users/me`.
- `POST /tasks`, `GET /tasks`, `GET /tasks/{id}`, `PATCH /tasks/{id}`, `DELETE /tasks/{id}`, `PATCH /tasks/{id}/status`.
- `GET /tasks/overdue` (deadline < now і status ∉ {done, cancelled}, з пагінацією).
- `GET /tasks/stats` — один SQL з `count(*) FILTER (...)` + `GROUP BY` для статусів/пріоритетів; нульові значення для всіх enum-ів.
- `POST /tasks/{id}/comments`, `GET /tasks/{id}/comments` (пагінація).
- `GET /tasks` query: `search` (ILIKE по title/description, trigram-індекс), `status[]`, `priority[]`, `assignee_id`, `deadline_from`, `deadline_to`, `sort_by ∈ {created_at, deadline, priority}`, `order ∈ {asc, desc}`, `page`, `size (≤100)`. Дефолт: `priority DESC, deadline ASC NULLS LAST, id`. Відповідь `Page[TaskRead]{items,total,page,size,pages}`.
- `/tasks/overdue` і `/tasks/stats` оголошуються **перед** `/tasks/{id}`.

## Background
- `workers/overdue.py`: `while True: await cancel_overdue(); await asyncio.sleep(settings.OVERDUE_CHECK_INTERVAL)`; всередині `pg_try_advisory_xact_lock(key)` → один bulk `UPDATE tasks SET status='cancelled' WHERE deadline < now() AND status NOT IN (...) RETURNING id`, лог кількості. Старт/cancel у `lifespan`, вимикається флагом `ENABLE_OVERDUE_WORKER` (off у тестах; `cancel_overdue()` тестується напряму).
- FastAPI `BackgroundTasks`: після зміни статусу/призначення — `notify_assignee(...)` (лог; точка розширення під email).

## Docker
- `Dockerfile` multi-stage на uv, non-root user.
- `docker-compose.yml`: `db` (postgres:16, healthcheck `pg_isready`, volume), `api` (depends_on: service_healthy; entrypoint: `alembic upgrade head && uvicorn`), `db_test` (tmpfs) для pytest.

## Плюси (в межах ТЗ)
1. Чисте шарування router/service/repository + доменні винятки з кодами помилок.
2. State machine переходів як дані (dict), параметризовані тести на всю матрицю переходів.
3. `SELECT FOR UPDATE` для ліміту 10 задач — захист від гонки.
4. Priority як smallint → дефолтне сортування індексоване, без CASE.
5. pg_trgm GIN-індекс для пошуку.
6. Stats одним запитом з `FILTER`.
7. Advisory lock у воркері — безпечно при кількох репліках/воркерах uvicorn.
8. Access + refresh JWT, argon2 через pwdlib (passlib не підтримується).
9. Тести проти реального Postgres, транзакція з rollback на кожен тест, time-machine для дедлайнів.
10. uv + ruff, `.env.example`, README з рішеннями по неоднозначностях ТЗ.

## Етапи
1. Скелет: pyproject (uv), config, db session, main/lifespan, Docker Compose, health endpoint.
2. Моделі + перша Alembic міграція (enum, pg_trgm extension, індекси).
3. Auth: register/login/refresh/me + тести.
4. Tasks CRUD + права + бізнес-правила редагування/видалення + тести.
5. Status endpoint + state machine + ліміт активних + BackgroundTasks нотифікації + тести.
6. Пошук/фільтри/сортування/пагінація + тести.
7. Коментарі + тести.
8. Overdue, stats + тести.
9. Periodic worker + тест `cancel_overdue`.
10. README, ruff, фінальний прогін.

## Верифікація
- `docker compose up --build` → API на `:8000`, міграції застосовуються автоматично, Swagger `/docs` з Authorize.
- `docker compose run --rm api pytest -q` — усі зелені (auth, CRUD, матриця переходів, done-без-assignee, прострочений done, ліміт 10, заборони редагування/видалення, фільтри+дефолтне сортування, overdue, stats, worker).
- Ручний smoke у Swagger: register → login → створити задачу → пройти backlog→…→done → коментар → stats.
- `ruff check .` без помилок.
