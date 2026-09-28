# Task Management API

[![CI](https://github.com/Cervin-van/task_menegement_api/actions/workflows/ci.yml/badge.svg)](https://github.com/Cervin-van/task_menegement_api/actions/workflows/ci.yml)

REST API для управління задачами: реєстрація та JWT-авторизація, задачі з виконавцями, статусами і дедлайнами,
коментарі, пошук і фільтрація, прострочені задачі, статистика, фонове автоскасування прострочених задач.

**Стек:** Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) · PostgreSQL 16 · Alembic · Pydantic v2 ·
Docker Compose · Pytest.

---

## Вимоги

- Docker + Docker Compose
- Для локальної розробки без Docker: Python 3.12, [uv](https://docs.astral.sh/uv/)

## Запуск

```bash
cp .env.example .env
docker compose up --build
```

| Адреса | Що це |
|---|---|
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/redoc | ReDoc |
| http://localhost:8000/health | стан сервісу і БД |

Сервіси:

| Сервіс | Призначення |
|---|---|
| `db` | PostgreSQL, на хості `127.0.0.1:5440` |
| `migrate` | застосовує міграції і завершується |
| `api` | HTTP API на порту `8000` |
| `worker` | фонове автоскасування прострочених задач |

Зупинка: `docker compose down` (з видаленням даних БД: `docker compose down -v`).

## Конфігурація (`.env`)

| Змінна | За замовчуванням | Опис |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@db:5432/tasks` | підключення до БД |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | `postgres` / `postgres` / `tasks` | параметри контейнера `db` |
| `DB_HOST_PORT` | `5440` | порт БД на хості |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | `10`, `10` | пул з'єднань на процес |
| `DB_STATEMENT_TIMEOUT_MS`, `DB_LOCK_TIMEOUT_MS`, `DB_IDLE_IN_TRANSACTION_TIMEOUT_MS` | `5000`, `3000`, `30000` | таймаути запиту, очікування блокування і простою транзакції |
| `JWT_SECRET_KEY` | — | **обов'язково**, не менше 32 символів |
| `JWT_ALGORITHM` | `HS256` | алгоритм підпису JWT |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | час життя access-токена |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | час життя refresh-токена |
| `UVICORN_WORKERS` | `2` | кількість процесів API |
| `OVERDUE_CHECK_INTERVAL_SECONDS` | `60` | період перевірки прострочених задач |
| `OVERDUE_BATCH_SIZE` | `500` | скільки задач воркер скасовує за одну транзакцію |
| `LOG_LEVEL` | `INFO` | рівень логування |

Згенерувати секрет: `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

---

## Використання

### Авторизація в Swagger

1. `POST /api/v1/auth/register` — зареєструватися.
2. Кнопка **Authorize** → `username` = email, `password` = пароль (інші поля порожні) → **Authorize**.
3. Усі наступні запити зі Swagger підуть з токеном. Після перезавантаження сторінки авторизуватися знову.

### Приклад через curl

```bash
# реєстрація
curl -X POST localhost:8000/api/v1/auth/register -H "Content-Type: application/json" \
  -d '{"email": "alice@example.com", "password": "Str0ngPass!", "full_name": "Alice"}'

# вхід → access_token, refresh_token
curl -X POST localhost:8000/api/v1/auth/login \
  -d "username=alice@example.com&password=Str0ngPass!"

# створити задачу
curl -X POST localhost:8000/api/v1/tasks -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"title": "Release", "priority": "high", "deadline": "2030-01-01T12:00:00Z"}'

# змінити статус
curl -X PATCH localhost:8000/api/v1/tasks/1/status -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" -d '{"status": "in_progress"}'
```

---

## API

Базовий префікс: `/api/v1`. Усі endpoints, крім реєстрації, входу і refresh, потребують
заголовок `Authorization: Bearer <access_token>`.

| Метод | Шлях | Опис |
|---|---|---|
| POST | `/auth/register` | реєстрація |
| POST | `/auth/login` | вхід (form: `username` = email, `password`) → access + refresh токени |
| POST | `/auth/refresh` | нова пара токенів за `refresh_token` |
| GET | `/users/me` | поточний користувач |
| POST | `/tasks` | створити задачу |
| GET | `/tasks` | список задач |
| GET | `/tasks/overdue` | прострочені задачі |
| GET | `/tasks/stats` | статистика |
| GET | `/tasks/{id}` | отримати задачу |
| PATCH | `/tasks/{id}` | редагувати задачу |
| PATCH | `/tasks/{id}/status` | змінити статус |
| DELETE | `/tasks/{id}` | видалити задачу |
| POST | `/tasks/{id}/comments` | додати коментар |
| GET | `/tasks/{id}/comments` | список коментарів |

### Задача

| Поле | Тип | Примітка |
|---|---|---|
| `title` | string, 1–255 | обов'язкове |
| `description` | string до 10 000 символів \| null | |
| `priority` | `low` \| `medium` \| `high` | за замовчуванням `medium` |
| `assignee_id` | int \| null | виконавець (id користувача) |
| `deadline` | datetime з таймзоною \| null | ISO 8601, напр. `2030-01-01T12:00:00Z` |
| `status` | див. нижче | змінюється лише через `/tasks/{id}/status` |
| `author`, `created_at`, `updated_at` | — | заповнюються автоматично |

### Список задач `GET /tasks`

| Параметр | Опис |
|---|---|
| `search` | пошук у назві та описі (без урахування регістру) |
| `status` | фільтр, можна кілька: `?status=todo&status=review` |
| `priority` | фільтр, можна кілька: `?priority=high` |
| `assignee_id` | фільтр за виконавцем |
| `deadline_from`, `deadline_to` | діапазон дедлайну (ISO 8601 з таймзоною) |
| `sort_by` | `created_at` \| `deadline` \| `priority` |
| `order` | `asc` \| `desc` (разом із `sort_by`) |
| `page`, `size` | пагінація, `size` ≤ 100 (за замовчуванням 20) |

Сортування за замовчуванням: пріоритет (high → medium → low), потім найближчий дедлайн.

Відповідь списків (`/tasks`, `/tasks/overdue`, коментарі):

```json
{"items": [...], "total": 42, "page": 1, "size": 20, "pages": 3}
```

### Статистика `GET /tasks/stats`

```json
{
  "total": 7,
  "by_status": {"backlog": 2, "todo": 1, "in_progress": 1, "review": 1, "done": 1, "cancelled": 1},
  "by_priority": {"low": 2, "medium": 2, "high": 3},
  "overdue": 2,
  "active": 3
}
```

---

## Статуси і правила

```mermaid
stateDiagram-v2
    [*] --> backlog
    backlog --> todo
    backlog --> in_progress
    todo --> in_progress
    in_progress --> review
    review --> done
    backlog --> cancelled
    todo --> cancelled
    in_progress --> cancelled
```

Активні статуси: `todo`, `in_progress`, `review`. Завершені: `done`, `cancelled`.

| Правило | Відповідь |
|---|---|
| Дозволені лише переходи зі схеми, повернення назад заборонене | 409 `INVALID_STATUS_TRANSITION` |
| `review` — тільки з виконавцем | 409 `REVIEW_REQUIRES_ASSIGNEE` |
| `done` — тільки з виконавцем | 409 `DONE_REQUIRES_ASSIGNEE` |
| `done` — тільки до дедлайну | 409 `DONE_AFTER_DEADLINE` |
| Дедлайн при створенні/зміні не в минулому | 422 `DEADLINE_IN_PAST` |
| У `review`/`done` виконавця змінити не можна | 409 `ASSIGNEE_LOCKED` |
| Задачі в `done`/`cancelled` не редагуються | 409 `TASK_NOT_EDITABLE` |
| Задачі в `in_progress`/`review` не видаляються | 409 `TASK_NOT_DELETABLE` |
| Не більше 10 активних задач на виконавця | 409 `ASSIGNEE_TASK_LIMIT_EXCEEDED` |
| Дедлайн простроченої задачі можна перенести, але не прибрати | 409 `OVERDUE_DEADLINE_REMOVAL_FORBIDDEN` |

Права доступу:

| Дія | Хто може |
|---|---|
| Переглядати задачі, список, статистику, коментувати | будь-який авторизований користувач |
| Редагувати задачу, змінювати статус | автор або виконавець |
| Змінювати виконавця і дедлайн, видаляти задачу | тільки автор |

Фоновий воркер кожні `OVERDUE_CHECK_INTERVAL_SECONDS` переводить у `cancelled` усі незавершені задачі
з минулим дедлайном і надсилає сповіщення автору та виконавцю (у лог сервісу `worker`).

### Помилки

Усі помилки мають однаковий формат:

```json
{"error": {"code": "INVALID_STATUS_TRANSITION", "message": "...", "details": {...}}}
```

| HTTP | Значення |
|---|---|
| 401 | немає або невалідний токен |
| 403 | немає прав на дію |
| 404 | ресурс не знайдено |
| 409 | порушення бізнес-правила |
| 422 | невалідні дані запиту (зокрема невідомі поля і параметри) |
| 503 | БД не встигла виконати запит (таймаут), можна повторити |

---

## Тести

У Docker (окрема тестова БД):

```bash
docker compose --profile test run --rm --build tests
```

Локально:

```bash
docker compose --profile test up -d db_test     # тестова БД на 127.0.0.1:5441
uv sync
uv run pytest -q
```

Тести запускаються лише проти БД, назва якої закінчується на `_test`.

Лінтер:

```bash
uv run ruff check . && uv run ruff format --check .
```

CI (GitHub Actions) на кожен push і pull request: lint, тести, збірка Docker-образу.

## Міграції

```bash
# застосувати (виконується автоматично сервісом migrate)
docker compose run --rm migrate

# створити нову (локально, БД з compose на 127.0.0.1:5440)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5440/tasks \
  uv run alembic revision --autogenerate -m "description"
```

## Структура проєкту

```
app/
  api/            HTTP-шар: роутери v1, залежності, обробка помилок, health
  core/           конфігурація, безпека (JWT, паролі), винятки, логування
  db/             підключення до БД, базова модель, кастомні типи
  domain/         статуси, правила переходів, доменні події
  models/         ORM-моделі: User, Task, Comment
  repositories/   запити до БД
  schemas/        Pydantic-схеми запитів і відповідей
  services/       бізнес-логіка
  workers/        фоновий воркер автоскасування
  notifications.py
alembic/          міграції
tests/            тести
```
