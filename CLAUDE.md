# CLAUDE.md

## Стек
- Frontend: Angular 20 (див. окремий репозиторій, Signals — Angular 20)
- Backend: FastAPI (Python 3.12)
- ORM: SQLAlchemy (async)
- Валідація: Pydantic v2

## Структура папок (бекенд)
- `app/` — основний код FastAPI
  - `models/` — SQLAlchemy моделі (успадковують `TimestampMixin` з `models/base.py`)
  - `routers/` — FastAPI роутери (тільки HTTP-шар, без бізнес-логіки)
  - `schemas/` — Pydantic-схеми (без DB-запитів!)
  - `services/` — бізнес-логіка (`auth_service.py`, `club_service.py`)
  - `dependencies.py` — залежності (`get_current_user`, `require_club_organizer`, `get_optional_user`)
  - `main.py` — точка входу (Sentry ініціалізація в lifespan)
  - `config.py` — налаштування (pydantic-settings; SECRET_KEY валідується при старті)
  - `database.py` — підключення до БД (pool_size=10, max_overflow=20)
- `alembic/` — міграції (2 файли: initial schema + FK indexes)
- `tests/` — pytest-тести (fixtures у conftest.py: `register_user`, `auth_headers`)

## Важливі правила архітектури
- DB-запити ТІЛЬКИ в `services/` або `routers/`, ніколи в `schemas/`
- `_require_organizer` → використовувати `require_club_organizer` з `dependencies.py`
- List endpoints мають `skip`/`limit` пагінацію (Query params)
- Path params UUID — тип `uuid.UUID`, не `str`
- WebSocket auth — токен передається як query param `?token=<jwt>`

## Як запускати тести та лінтер
- Тести: `pytest`
- Лінтер: `ruff check .`
- Типізація: `mypy app/`
- Міграції: `alembic upgrade head`

## Project Context
This project uses **Repomix** to provide a full map of the codebase.

## Context Commands
- **Refresh Map:** Run `npm run build-ctx` to update the project context.
- **Project Map File:** `repomix-output.md`

## Development Rules
- Frontend: Angular 20 (Signals — Angular 20, modern standalone components).
- Backend: FastAPI (Pydantic v2, Async).
- Always check `repomix-output.md` before asking about the file structure.
- If a file is not in repomix-output.md, assume it doesn't exist yet.  # Rules of Engagement

## Автоматичні карти проекту
- [API_SPEC.md](./API_SPEC.md) — OpenAPI-специфікація
- [repomix-output.md](./repomix-output.md) — карта структури проекту (генерується Repomix)

## Інше
- Dockerfile — інструкції для збирання образу
- .gitignore — ігнорування зайвих файлів
- .pre-commit-config.yaml — хуки для якості коду (наприклад, ruff, black — Claude має враховувати автозапуск форматування/ліна)

---

> Для оптимізації контексту Claude, не скануйте вручну директорії — використовуйте цей файл як путівник.
