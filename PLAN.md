# PLAN — Task Management API

Репозиторій: https://github.com/Cervin-van/task_menegement_api.git
Статус: **усі етапи виконані** (122 тести зелені). Деталі для рев'юера — `README.md`.

## Узгоджені рішення по неоднозначностях ТЗ
- Статуси: `backlog, todo, in_progress, review, done, cancelled` (Todo додано — фігурує в правилах ТЗ).
- Ліміт 10 активних (todo/in_progress/review) — для виконавця.
- Права: редагувати/статус — автор або виконавець; видаляти — тільки автор.
- Автоскасування — системний перехід з будь-якого нефінального статусу (включно з review).
- Дедлайн порівнюється з поточним моментом; при редагуванні — лише якщо змінюється.
- Коментарі дозволені й до done/cancelled. Список, overdue, stats — по всіх задачах.

## Архітектура
```
app/
  main.py               create_app(), lifespan (dispose engine), error handlers, logging
  api/                  deps.py, errors.py (єдиний формат помилок), health.py
  api/v1/               auth.py, users.py, tasks.py, comments.py, router.py
  core/                 config.py, security.py (argon2, JWT access/refresh), exceptions.py, logging.py
  db/                   base.py (naming convention), session.py (pool з env), types.py (PriorityType)
  domain/               status.py (enums, ALLOWED_TRANSITIONS, константи), events.py (доменні події)
  models/               user.py, task.py, comment.py (lazy="raise", eager_defaults)
  repositories/         user.py (lock FOR UPDATE), task.py (list/overdue/stats, overdue_condition), comment.py
  schemas/              auth.py, user.py, task.py, comment.py, common.py (PageParams, Page[T])
  services/             auth.py, task.py (усі бізнес-правила), comment.py
  workers/overdue.py    cancel_overdue (advisory lock + bulk UPDATE), run_worker, main (SIGTERM)
  notifications.py      dispatch_events → лог (BackgroundTasks)
alembic/                async env, render_item для TypeDecorator, disable_existing_loggers=False
tests/                  conftest (міграції, savepoint-ізоляція, фабрики) + тести по фічах
```

Docker Compose: `db` → `migrate` (one-shot) → `api` + `worker`; профіль `test`: `db_test` (tmpfs) + `tests`.

## Бізнес-правила (`services/task.py`)
| Правило | Код |
|---|---|
| Переходи тільки за `ALLOWED_TRANSITIONS`; назад — помилка | 409 `INVALID_STATUS_TRANSITION` |
| Done: потрібен виконавець / дедлайн не минув | 409 `DONE_REQUIRES_ASSIGNEE` / `DONE_AFTER_DEADLINE` |
| Дедлайн не в минулому (create/update) | 422 `DEADLINE_IN_PAST` |
| Виконавець незмінний у review/done | 409 `ASSIGNEE_LOCKED` |
| Done/Cancelled не редагуються | 409 `TASK_NOT_EDITABLE` |
| In progress/Review не видаляються | 409 `TASK_NOT_DELETABLE` |
| ≤10 активних на виконавця (`FOR UPDATE` рядка юзера) | 409 `ASSIGNEE_TASK_LIMIT_EXCEEDED` |
| Права автор/виконавець | 403 `PERMISSION_DENIED` |

## Плюси (в межах ТЗ)
1. Шари router → service → repository, доменні винятки з кодами, єдиний формат помилок.
2. Стан-машина як дані + параметризований тест усієї матриці 6×6.
3. `SELECT FOR UPDATE` на задачу і на виконавця — без гонок при лімітах і змінах статусу.
4. Priority як smallint (TypeDecorator) → індексоване дефолтне сортування без CASE.
5. pg_trgm GIN для пошуку + екранування LIKE-wildcards.
6. Stats одним запитом з `count(*) FILTER`.
7. Воркер окремим сервісом, `pg_try_advisory_xact_lock`, bulk `UPDATE ... RETURNING`, graceful shutdown.
8. Доменні події → BackgroundTasks (доставка ізольована в `notifications.py`).
9. JWT access + refresh з типом токена, argon2 (pwdlib), захист від timing-перебору email.
10. Тести на реальному Postgres через міграції, savepoint-ізоляція, time-machine.
11. Масштабування: stateless API, one-shot migrate, пул БД з env, multi-stage non-root образ, uv.lock.

## Етапи
| # | Етап | Коміт |
|---|---|---|
| 0 | CLAUDE.md, PLAN.md, .gitignore | `docs: add project plan and CLAUDE.md` |
| 1 | Скелет, Docker Compose, health | `feat: project skeleton with docker compose and health check` |
| 2 | Моделі, енуми, перша міграція, `migrate` | `feat: add models, domain enums and initial alembic migration` |
| 3 | Auth (register/login/refresh/me) | `feat: jwt auth (register, login, refresh, me) with tests` |
| 4 | Tasks CRUD, права, правила редагування/видалення | `feat: tasks crud with permissions and edit/delete rules` |
| 5 | Зміна статусу, ліміт, нотифікації | `feat: status transitions, active tasks limit and background notifications` |
| 6 | Пошук, фільтри, сортування, пагінація | `feat: task list with search, filters, sorting and pagination` |
| 7 | Коментарі | `feat: task comments with pagination` |
| 8 | Overdue, статистика | `feat: overdue tasks endpoint and task statistics` |
| 9 | Воркер автоскасування | `feat: periodic worker auto-cancelling overdue tasks` |
| 10 | README, фінальний прогін | `docs: readme with setup, architecture and tz decisions` |

## Верифікація
- `docker compose up --build` → `/docs`, міграції на чистій БД застосовуються сервісом `migrate`.
- `docker compose --profile test run --rm --build tests` → усі тести зелені.
- `uv run ruff check . && uv run ruff format --check .`
