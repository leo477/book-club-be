# Architecture Review — book-club-be (FastAPI)

**Дата:** 2026-06-15
**Скоуп:** шарова архітектура (routers/services/repositories/models/schemas), узгодженість шарів, DI, async-патерни, tooling, тести.
**Метод:** статичний аналіз дерева, підрахунок прямих DB-звернень по шарах (`db.execute`/`select()`), перевірка реальної задіяності модулів grep'ом, читання `main.py`/`dependencies.py`/`pyproject.toml`.
**Парний документ:** фронтенд — `ARCHITECTURE-REVIEW.md` у репо `book-club-fe`.

## Загальний вердикт: **7.5 / 10**
Правильна шарова FastAPI-архітектура з дорослим tooling-ом, але з однією суттєвою непослідовністю: **оголошений шар репозиторіїв не задіяний, а роутери «товсті» (містять сирий data-access).**

---

## Структура
```
app/
├── routers/       (15)  auth · clubs · members · events · quizzes · chat · users · books · geocode · randomizer · routes · upload · config · health
├── services/      (9)   auth · club · event · quiz · chat · geocoding · routing · google_books
├── repositories/  (4)   chat · club · event · quiz     ← оголошені, але НЕ використовуються
├── models/        (11)  SQLAlchemy ORM
├── schemas/       (10)  Pydantic v2 (API DTO)
├── config.py · database.py · dependencies.py · exceptions.py · limiter.py · main.py
alembic/versions/  (18 міграцій)
tests/             (28 файлів)
```
Стек: async SQLAlchemy 2.0 + asyncpg, Pydantic v2 + pydantic-settings, structlog, Sentry, Prometheus, slowapi (rate-limit), Redis. `mypy --strict`, `ruff` (E/F/I/N/UP/ANN/S/B/A/C4/T20/RUF), bandit, pre-commit, CVE-піни транзитивних залежностей.

---

## 🔴 BE-1 (High) — Шар репозиторіїв є мертвим кодом
`app/repositories/{chat,club,event,quiz}.py` (324 рядки) визначені й реекспортовані в `repositories/__init__.py`, але **не імпортуються ніде** в кодовій базі (єдиний імпорт `app.repositories.*` — у власному `__init__.py`).

Замість них data-access роблять напряму роутери й сервіси. Прямі DB-звернення (`db.execute`/`select()`):

| Шар | Файл | К-сть сирих запитів |
|---|---|---|
| router | `chat.py` | 47 |
| router | `clubs.py` | 38 |
| router | `quizzes.py` | 37 |
| router | `events.py` | 19 |
| router | `members.py` / `auth.py` | 12 / 12 |
| service | `club_service.py` | 69 |
| service | `event_service.py` | 23 |

Роль «доступу до даних» **дублюється** між роутерами і сервісами, а призначений для цього репозиторій не підключений → структура вводить в оману.

**Рекомендація — обрати одне (послідовність важливіша за вибір):**
- **(A) Прийняти патерн:** весь data-access — через репозиторії. Роутер = HTTP-маршалінг (валідація → виклик сервісу → відповідь), сервіс = бізнес-логіка, репозиторій = запити. Еталон узгодженості вже є — `users.py` (0 сирих запитів, чисте делегування).
- **(B) Видалити репозиторії** як мертву абстракцію і чесно лишити `router → service → model`.

---

## 🟠 Інші зауваження
| # | Severity | Що | Чому проблема | Рекомендація |
|---|---|---|---|---|
| BE-2 | Medium | «Товсті» роутери із сирими запитами (`chat`/`clubs`/`quizzes`) | бізнес/data-логіка в HTTP-шарі → важко тестувати й перевикористовувати | винести запити в єдиний data-шар (див. BE-1), роутери лишити тонкими |
| BE-3 | Medium | Функціонально-локальні імпорти в `dependencies.py` / `get_current_user` (`from app.services... import` усередині функції) | обхід циклічних залежностей — сигнал зайвого зчеплення models↔services↔deps | розплутати залежності, підняти імпорти на рівень модуля |
| BE-4 | Low | Фоновий cleanup-таск (`while True` + `asyncio.sleep(86400)`) живе в `main.py` | на multi-instance деплої (Render scaling) запуститься на кожному інстансі → дубльована робота | винести в окремий модуль; для масштабування — зовнішній планувальник / лідер-локи |
| BE-5 | Nit | Непослідовний стиль імпортів роутерів у `main.py` (`from app.routers import clubs` vs `from app.routers.auth import router as ...`) | косметика/читабельність | уніфікувати |

---

## Сильні сторони
- Чистий поділ **schemas (Pydantic v2 DTO)** vs **models (ORM)** vs **routers** — без протікання ORM у відповіді API.
- `get_current_user` кешує користувача в `request.state` — уникає повторних запитів у межах одного реквесту.
- Серйозний tooling: `mypy --strict`, security-лінт (bandit + ruff-`S`), CVE-піни транзитивних залежностей, pre-commit, спостережуваність (Sentry / Prometheus / structlog), коректний rate-limiting (slowapi + Redis).
- Реальне покриття тестами (28 файлів, включно з тестами міграцій і WebSocket-безпеки).
- Async наскрізно (SQLAlchemy 2.0 async + asyncpg), Alembic із 18 версійними міграціями.

---

## Підсумковий пріоритет
1. **BE-1 / BE-2** — вирішити долю репозиторіїв і витягнути сирі запити з роутерів у єдиний data-шар (найбільший вплив на підтримуваність і тестованість).
2. **BE-3** — розплутати циклічні залежності, прибрати локальні імпорти.
3. **BE-4** — винести фоновий таск; на Render зі скейлом це реальний ризик дублювання.
4. **BE-5** — косметична уніфікація імпортів.
