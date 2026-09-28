# Task Management API

REST API для управління задачами: реєстрація/JWT, CRUD задач, виконавці, коментарі, стан-машина статусів,
пошук/фільтри/сортування/пагінація, прострочені задачі, статистика і фоновий воркер автоскасування.

**Стек:** FastAPI · SQLAlchemy 2.0 (async, asyncpg) · PostgreSQL 16 · Alembic · Pydantic v2 · Docker Compose ·
Pytest · BackgroundTasks · uv · ruff.

---

## Швидкий старт

```bash
cp .env.example .env
docker compose up --build
```

- Swagger: http://localhost:8000/docs → **Authorize** (username = email, password) після реєстрації.
- Health: http://localhost:8000/health

Порядок старту: `db` (healthcheck) → `migrate` (`alembic upgrade head`, one-shot) → `api` + `worker`.

### Тести

```bash
docker compose --profile test run --rm --build tests      # 122 тести проти окремого Postgres (tmpfs)
```

Локально (Python 3.12+, [uv](https://docs.astral.sh/uv/)):

```bash
docker compose --profile test up -d db_test               # 127.0.0.1:5441
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

---

## Сервіси Docker Compose

| Сервіс | Що робить | Масштабування |
|---|---|---|
| `db` | PostgreSQL 16, volume `pgdata`, порт `127.0.0.1:5440` | — |
| `migrate` | `alembic upgrade head` один раз на деплой | один екземпляр |
| `api` | Uvicorn, stateless | `UVICORN_WORKERS`, репліки за балансувальником |
| `worker` | автоскасування прострочених задач | safe при N екземплярах (advisory lock) |
| `db_test`, `tests` | профіль `test` | — |

Один образ (multi-stage, uv, non-root) для `migrate`/`api`/`worker` — різні лише команди.
Міграції винесені з entrypoint API, щоб репліки не ганяли їх паралельно; воркер винесений з `lifespan` API,
щоб не множитись разом з репліками. Пул БД — `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` на процес:
сумарно `репліки × воркери × (pool + overflow)` має вкладатися в `max_connections`.

---

## API (`/api/v1`)

| Метод | Шлях | Опис |
|---|---|---|
| POST | `/auth/register` | реєстрація → 201 |
| POST | `/auth/login` | OAuth2 form (`username`=email) → access + refresh |
| POST | `/auth/refresh` | нова пара токенів за refresh-токеном |
| GET | `/users/me` | поточний користувач |
| POST | `/tasks` | створити задачу (завжди `backlog`) |
| GET | `/tasks` | список: пошук, фільтри, сортування, пагінація |
| GET | `/tasks/overdue` | прострочені (дедлайн минув, не done/cancelled) |
| GET | `/tasks/stats` | статистика |
| GET | `/tasks/{id}` | задача |
| PATCH | `/tasks/{id}` | часткове редагування (без статусу) |
| PATCH | `/tasks/{id}/status` | зміна статусу |
| DELETE | `/tasks/{id}` | видалення → 204 |
| POST | `/tasks/{id}/comments` | додати коментар |
| GET | `/tasks/{id}/comments` | коментарі (хронологічно, пагінація) |

### `GET /tasks`

| Параметр | Приклад |
|---|---|
| `search` | пошук по назві та опису без урахування регістру |
| `status` (кілька) | `?status=todo&status=review` |
| `priority` (кілька) | `?priority=high` |
| `assignee_id` | `?assignee_id=3` |
| `deadline_from`, `deadline_to` | ISO 8601 з таймзоною |
| `sort_by` | `created_at` \| `deadline` \| `priority` |
| `order` | `asc` \| `desc` (за замовчуванням: created_at/priority — desc, deadline — asc) |
| `page`, `size` | `size` ≤ 100 |

Без `sort_by`: **priority (High → Medium → Low), потім найближчий дедлайн**, задачі без дедлайну — в кінці.
Відповідь: `{"items": [...], "total": 42, "page": 1, "size": 20, "pages": 3}`. Невідомий параметр → 422.

### Формат помилок

```json
{"error": {"code": "INVALID_STATUS_TRANSITION", "message": "Transition 'review' -> 'todo' is not allowed",
           "details": {"from": "review", "to": "todo", "allowed": ["done"]}}}
```

`401` не автентифіковано · `403` немає прав · `404` не знайдено · `409` порушено бізнес-правило · `422` валідація.

---

## Бізнес-логіка

```mermaid
stateDiagram-v2
    [*] --> backlog
    backlog --> todo
    todo --> in_progress
    in_progress --> review
    review --> done
    backlog --> cancelled
    todo --> cancelled
    in_progress --> cancelled
    review --> cancelled: тільки воркер (дедлайн минув)
```

| Правило | Код помилки |
|---|---|
| Лише переходи зі схеми; повернення назад — помилка | 409 `INVALID_STATUS_TRANSITION` |
| `done` тільки з виконавцем | 409 `DONE_REQUIRES_ASSIGNEE` |
| `done` не можна після дедлайну | 409 `DONE_AFTER_DEADLINE` |
| Дедлайн при створенні/редагуванні не в минулому | 422 `DEADLINE_IN_PAST` |
| У `review`/`done` виконавця змінювати не можна | 409 `ASSIGNEE_LOCKED` |
| `done`/`cancelled` не редагуються (ні поля, ні статус) | 409 `TASK_NOT_EDITABLE` |
| `in_progress`/`review` не видаляються | 409 `TASK_NOT_DELETABLE` |
| ≤ 10 активних задач (todo/in_progress/review) на виконавця | 409 `ASSIGNEE_TASK_LIMIT_EXCEEDED` |
| Редагувати/міняти статус — автор або виконавець; видаляти — автор | 403 `PERMISSION_DENIED` |

### Рішення по неоднозначностях ТЗ

- **Статус `Todo`.** У переліку статусів його немає, але він фігурує в правилах переходів і ліміті активних задач →
  додано: `backlog → todo → in_progress → review → done`.
- **Ліміт 10 активних** рахується для **виконавця** (це його навантаження). Перевіряється при призначенні на
  активну задачу і при переході `backlog → todo`.
- **Права** в ТЗ не описані: редагувати і змінювати статус — автор або виконавець, видаляти — тільки автор.
  Переглядати задачі, список, статистику і коментувати може будь-який автентифікований користувач.
- **Автоскасування** — системний перехід з будь-якого незавершеного статусу, включно з `review`
  (для користувача `review → cancelled` заборонено). ТЗ прямо вимагає скасовувати все, що не завершено до дедлайну.
- **Дедлайн «не менший за поточну дату»** трактується як поточний момент часу (datetime з таймзоною;
  naive datetime → 422). При редагуванні перевіряється лише якщо дедлайн змінюється — інакше задачу з уже
  простроченим дедлайном неможливо було б відредагувати взагалі.
- **Коментарі** дозволені й до `done`/`cancelled`: коментар — обговорення, а не редагування задачі.
- **Статус змінюється тільки** через `PATCH /tasks/{id}/status`; поле `status` у `PATCH /tasks/{id}` → 422.
- Нова задача завжди створюється в `backlog`.

---

## Архітектура

```
api/v1 (routers, тонкі) → services (бізнес-правила) → repositories (запити) → models
                              │
                              └─ domain events ──► BackgroundTasks ──► notifications (лог)
workers/overdue.py ── advisory lock ── bulk UPDATE ... RETURNING
```

Ключові рішення:

- **Стан-машина як дані** — `ALLOWED_TRANSITIONS` у `app/domain/status.py`; тест перевіряє всі 36 пар переходів.
- **Гонки.**
  - Редагування і зміна статусу беруть задачу через `SELECT ... FOR UPDATE`.
  - Ліміт 10 задач: спершу `FOR UPDATE` на рядок виконавця, потім `count`. Два паралельні призначення не проскочать ліміт.
  - Воркер і користувач: якщо користувач тримає лок і закриває задачу, `UPDATE` воркера чекає.
    Потім під READ COMMITTED він переперевіряє `WHERE` і не скасує вже завершену задачу.
- **Воркер** (`app/workers/overdue.py`).
  - `pg_try_advisory_xact_lock`: неблокуючий, знімається сам на commit/rollback, тож падіння процесу не залишить завислого лока.
  - Один `UPDATE ... RETURNING` замість завантаження кожного рядка окремо.
  - Graceful shutdown по SIGTERM.
- **Нотифікації.** Сервіс після успішного `commit` складає доменні події (`TaskAssigned`, `TaskStatusChanged`).
  Роутер віддає їх у FastAPI `BackgroundTasks`. Доставка зараз — структурований лог. Щоб перейти на email чи
  чергу, треба змінити лише `app/notifications.py`. Той, хто зробив зміну, повідомлення не отримує.
- **«Прострочена задача»** визначається в одному місці, `overdue_condition(now)`. Цю умову використовують endpoint,
  статистика і воркер.
- **Статистика одним запитом.**
  - `count(*) FILTER (WHERE ...)` на кожну метрику, статус і пріоритет — один прохід по таблиці.
  - Значення, яких немає в базі, повертаються нулями.
- **Індекси.**
  - `(priority DESC, deadline ASC NULLS LAST)` — під сортування за замовчуванням.
  - Пріоритет зберігається як `smallint` (1/2/3) через `TypeDecorator`, тож сортування не потребує `CASE`.
  - GIN `pg_trgm` на `title`/`description` — під `ILIKE '%q%'`. Спецсимволи `%`, `_`, `\` у пошуковому запиті екрануються.
  - `(task_id, created_at)` — для коментарів.
  - Окремі індекси на `status`, `assignee_id`, `deadline`, `author_id`.
- **Стабільна пагінація** — останнім ключем сортування завжди йде `id`.
- **Auth.**
  - Пара токенів access і refresh. Поле `type` у токені не дає використати refresh-токен як access і навпаки.
  - Паролі хешуються argon2 (`pwdlib`; passlib більше не підтримується).
  - Для невідомого email пароль перевіряється проти фіктивного хешу, тож за часом відповіді не видно, які email зареєстровані.
- **Async-безпека.** Усі зв'язки моделей — `lazy="raise"`, тож кожне завантаження явне (`selectinload`).
  `eager_defaults` повертає `created_at`/`updated_at` одразу при записі через `RETURNING`.
- **Тести.**
  - Справжній Postgres, схема будується міграціями (`downgrade base → upgrade head`).
  - Кожен тест іде в транзакції з відкатом; `commit` у сервісах стає savepoint.
  - Час підміняється через `time-machine`.
  - 122 тести, серед них повна матриця переходів статусів, гонка за advisory lock і graceful stop воркера.

### Структура

```
app/
  api/            deps.py, errors.py, health.py, v1/{auth,users,tasks,comments,router}.py
  core/           config.py, security.py, exceptions.py, logging.py
  db/             base.py (naming convention), session.py, types.py (PriorityType)
  domain/         status.py (enums, стан-машина, константи), events.py
  models/         user.py, task.py, comment.py
  repositories/   user.py, task.py, comment.py
  schemas/        auth.py, user.py, task.py, comment.py, common.py (Page[T])
  services/       auth.py, task.py, comment.py
  workers/        overdue.py
  notifications.py
alembic/          async env, міграції
tests/            conftest.py + тести по фічах
```

### Свідомо поза scope

Відкликання refresh-токенів (blacklist по `jti`), ролі/адмінка, реальна доставка email, rate limiting,
курсорна пагінація.
