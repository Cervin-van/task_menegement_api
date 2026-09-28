# CLAUDE.md — Task Management API

Тестове завдання: REST API на FastAPI для управління задачами. Повний план — `PLAN.md`.
Репозиторій: https://github.com/Cervin-van/task_menegement_api.git

## Спілкування
- Українською, коротко, без води. Я Middle: базу не пояснювати, розкривати архітектуру і неочевидні рішення.

## Git
- **Коміти і push робить користувач сам.** Не виконувати `git commit`, `git push`, `git tag`, не змінювати історію.
- Можна: `git status`, `git diff`, `git log` (read-only). За потреби — запропонувати текст коміту (Conventional Commits), але не комітити.

## Scope
- Робимо **тільки ТЗ** + плюси зі списку в `PLAN.md` («Плюси»).
- Будь-що поза цим (нові сутності, сервіси, бібліотеки) — спершу питати.

## Узгоджені рішення по ТЗ
- Статуси: `backlog, todo, in_progress, review, done, cancelled` (Todo додано, бо фігурує в правилах ТЗ; `backlog → in_progress` теж дозволено — приклад із ТЗ).
- Ліміт 10 активних задач (todo/in_progress/review) — для **виконавця**.
- Права: редагувати/міняти статус — автор або виконавець; змінювати виконавця, дедлайн і видаляти — тільки автор; інакше 403. Дедлайн простроченої задачі можна перенести, не прибрати.
- `review` вимагає виконавця (інакше задача застрягає). Воркер надсилає ті самі `TaskStatusChanged`, що й ручна зміна.
- Періодична задача — окремий контейнер `worker` (той самий образ, asyncio-loop) + Postgres advisory lock; FastAPI `BackgroundTasks` — нотифікації (лог).
- Автоскасування прострочених воркером — системний перехід з будь-якого нефінального статусу.

## Стек
Python 3.12, FastAPI, SQLAlchemy 2.0 async + asyncpg, Alembic (async env), Pydantic v2 + pydantic-settings,
PyJWT, pwdlib[argon2], PostgreSQL 16, Docker Compose, uv, ruff, pytest + pytest-asyncio + httpx, time-machine.

## Архітектура і правила коду
- Шари: `api/v1` (router) → `services` → `repositories` → `models`.
- Бізнес-правила **тільки** в `services/`; роутери тонкі (валидація вводу, виклик сервісу, серіалізація).
- Репозиторії — тільки запити, без бізнес-логіки.
- Доменні винятки (`core/exceptions.py`) → єдиний exception handler → формат
  `{"error": {"code": "...", "message": "...", "details": {...}}}`.
  Коди: 403 права, 404 не знайдено, 409 порушення бізнес-правила, 422 валідація.
- Всі datetime — tz-aware UTC, у БД `timestamptz`.
- Priority зберігається як smallint (1 low / 2 medium / 3 high).
- Статусні переходи — тільки через `ALLOWED_TRANSITIONS` у `domain/status.py`.
- Схема БД змінюється тільки через Alembic (тести теж ганяють міграції, не `create_all`). Autogenerate завжди переглядати руками: extensions, drop enum у downgrade.
- ORM relationships — `lazy="raise"`: завантаження тільки явно (`selectinload`/`joinedload`).
- Кожне бізнес-правило покрите тестом.
- «Прострочена задача» визначається тільки через `overdue_condition(now)` (`repositories/task.py`); `now` — з Python, не `now()` БД.
- Нотифікації — через доменні події (`domain/events.py`) → `service.pop_events()` → `BackgroundTasks`; сервіс не знає про доставку.
- Row-локи — `with_for_update(key_share=True)` (`FOR NO KEY UPDATE`), не `FOR UPDATE`: інакше блокуються FK-вставки (коментарі, нові задачі).
- `JWT_SECRET_KEY` обов'язковий (≥ 32 символи) — без `.env` застосунок/alembic не стартують.
- Вхідні дані: `SafeStr` (без NUL), `DbDatetime` (aware, без epoch, рік 2..9998), `DbId` (strict у тілі) / `DbIdQuery` (query); `extra="forbid"` на всіх вхідних моделях. Не вмикати `strict=True` на всю модель — FastAPI валідує dict у python-режимі, і рядки-дати/енуми відпадуть.
- Сесія БД — `Depends(get_session, scope="function")`: закривається до відправки відповіді.
- Не називати методи класів іменами builtins (`list`, `id`, ...) — ламає анотації в тілі класу.

## Тести
- Реальний Postgres (`db_test`, tmpfs), схема — `alembic downgrade base && upgrade head` на старті сесії.
- Ізоляція: зовнішня транзакція на тест + `join_transaction_mode="create_savepoint"` → `commit` у сервісах = savepoint; **не робити `session.rollback()` у коді без потреби** — в тестах відкотить сід.
- Час — `time_machine.travel`; дані для списків/сортування — сід напряму через `session`.
- Реальна конкуренція — `tests/test_concurrency.py`: окремі сесії з реальними commit + прибирання; вікно гонки розширюється `monkeypatch`-затримкою.
- CI: `.github/workflows/ci.yml` (lint, test з Postgres service, docker build); Python пінований `.python-version`.

## Масштабування
- API stateless (JWT, без in-memory стану) → горизонтально: репліки + `UVICORN_WORKERS`.
- Міграції — one-shot сервіс `migrate`, не в entrypoint кожної репліки API.
- Воркер — окремий сервіс, не в lifespan API (інакше дублюється на кожну репліку); advisory lock — страховка.
- Пул БД налаштовується env (`DB_POOL_SIZE`, `DB_MAX_OVERFLOW`); сумарно ≤ `max_connections` Postgres.
- Важкі запити (список, stats, overdue) — тільки через індекси, агрегати в SQL, пагінація обов'язкова.

## Команди
```bash
cp .env.example .env
docker compose up --build                                   # db → migrate → api + worker
# Нова міграція — локально (runtime-образ без ruff для post-write hook); БД з compose на 127.0.0.1:5440
$env:DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:5440/tasks"; uv run alembic revision --autogenerate -m "msg"
uv run alembic check                                        # моделі == міграції
docker compose --profile test run --rm tests       # тести проти окремого db_test
uv sync && uv run ruff check .                              # локально
```
