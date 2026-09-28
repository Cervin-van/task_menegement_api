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
- Статуси: `backlog, todo, in_progress, review, done, cancelled` (Todo додано, бо фігурує в правилах ТЗ).
- Ліміт 10 активних задач (todo/in_progress/review) — для **виконавця**.
- Права: редагувати/міняти статус — автор або виконавець; видаляти — тільки автор; інакше 403.
- Періодична задача — asyncio-loop у `lifespan` + Postgres advisory lock; FastAPI `BackgroundTasks` — нотифікації (лог).
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
- Схема БД змінюється тільки через Alembic (`create_all` — лише в тестах, якщо треба).
- Кожне бізнес-правило покрите тестом.

## Команди
```bash
docker compose up --build                                   # запуск (міграції застосовуються в entrypoint)
docker compose exec api alembic upgrade head
docker compose exec api alembic revision --autogenerate -m "msg"
docker compose run --rm api pytest -q
ruff check . && ruff format .
```
