# Backend API Endpoints — Book Club

> Аналіз фронтенду Angular 20 (сервіси, моделі, моки).
> Наразі весь стан — in-memory mock. Цей документ описує REST API, який потрібен на беку.

---

## Базові домовленості

- Base URL: `/api/v1`
- Auth: JWT (Bearer token у заголовку `Authorization`, або httpOnly cookie `access_token`)
- Формат дат: ISO 8601 (`2026-04-15T18:00:00Z`)
- Pagination: `?page=1&limit=20` (де потрібно)
- Помилки: `{ "error": "message", "code": "ERROR_CODE" }`

---

## 1. AUTH

### POST `/api/v1/auth/register`
Реєстрація нового користувача.

**Request body:**
```json
{
  "email": "string",
  "password": "string",
  "displayName": "string",
  "role": "user" | "organizer"
}
```
**Response 201:**
```json
{
  "user": {
    "id": "string",
    "email": "string",
    "displayName": "string",
    "role": "user" | "organizer",
    "avatarUrl": null,
    "createdAt": "ISO date"
  },
  "accessToken": "string"
}
```
**Errors:** 409 email already exists

---

### POST `/api/v1/auth/login`
Вхід існуючого користувача.

**Request body:**
```json
{
  "email": "string",
  "password": "string"
}
```
**Response 200:**
```json
{
  "user": { ...UserProfile },
  "accessToken": "string"
}
```
**Errors:** 401 invalid credentials

---

### POST `/api/v1/auth/logout`
Вихід. Інвалідує токен/сесію.

**Response 204:** (no body)

---

### GET `/api/v1/auth/me`
Відновлення сесії при перезавантаженні сторінки.

**Headers:** `Authorization: Bearer <token>`

**Response 200:**
```json
{
  "id": "string",
  "email": "string",
  "displayName": "string",
  "role": "user" | "organizer",
  "avatarUrl": "string | null",
  "createdAt": "ISO date",
  "socialsPublic": true,
  "socials": {
    "telegram": "string | null",
    "instagram": "string | null",
    "twitter": "string | null",
    "linkedin": "string | null",
    "github": "string | null",
    "goodreads": "string | null"
  }
}
```
**Errors:** 401

---

## 2. USER / PROFILE

### GET `/api/v1/users/me/stats`
Статистика поточного користувача.

**Response 200:**
```json
{
  "clubsJoined": 3,
  "quizzesTaken": 12,
  "quizWins": 5,
  "likesReceived": 24,
  "booksRead": 18
}
```

---

### PATCH `/api/v1/users/me`
Оновлення профілю (ім'я).

**Request body (partial):**
```json
{
  "displayName": "string"
}
```
**Response 200:** `{ ...UserProfile }`

---

### PATCH `/api/v1/users/me/role`
Зміна ролі між `user` ↔ `organizer`.

**Request body:**
```json
{ "role": "user" | "organizer" }
```
**Response 200:** `{ ...UserProfile }`

---

### PATCH `/api/v1/users/me/socials`
Оновлення соціальних мереж.

**Request body:**
```json
{
  "telegram": "string | null",
  "instagram": "string | null",
  "twitter": "string | null",
  "linkedin": "string | null",
  "github": "string | null",
  "goodreads": "string | null"
}
```
**Response 200:** `{ ...UserProfile }`

---

### PATCH `/api/v1/users/me/socials-visibility`
Публічність соціальних мереж.

**Request body:**
```json
{ "socialsPublic": true }
```
**Response 200:** `{ ...UserProfile }`

---

## 3. CLUBS

### GET `/api/v1/clubs`
Список усіх публічних клубів (+ клуби поточного юзера, навіть приватні).

**Query params:**
- `search` — рядок пошуку по name/description
- `city` — фільтр по місту

**Response 200:**
```json
[
  {
    "id": "string",
    "name": "string",
    "description": "string | null",
    "coverUrl": "string | null",
    "organizerId": "string",
    "isPublic": true,
    "memberCount": 12,
    "createdAt": "ISO date",
    "city": "string",
    "nextMeetingDate": "ISO date | null",
    "address": "string | null",
    "lat": "number | null",
    "lng": "number | null",
    "theme": "string | null",
    "currentBook": {
      "title": "string",
      "author": "string",
      "description": "string"
    } | null,
    "memberPreviews": ["string"],
    "status": "active" | "paused" | "cancelled",
    "cancelledAt": "ISO date | undefined",
    "tags": ["string"],
    "meetingDurationMinutes": "number | null",
    "afterMeetingVenue": {
      "name": "string",
      "address": "string",
      "description": "string | undefined",
      "lat": "number | undefined",
      "lng": "number | undefined"
    } | null,
    "meetingHistory": [
      {
        "id": "string",
        "date": "ISO date",
        "status": "held" | "cancelled" | "rescheduled",
        "notes": "string | undefined"
      }
    ]
  }
]
```

---

### GET `/api/v1/clubs/my`
Клуби, до яких належить поточний юзер (як учасник або організатор).

**Response 200:** `Club[]` (той самий формат)

---

### GET `/api/v1/clubs/:id`
Деталі одного клубу.

**Response 200:** `Club` (повний об'єкт)
**Errors:** 404

---

### POST `/api/v1/clubs`
Створення клубу (тільки для `organizer`).

**Request body:**
```json
{
  "name": "string",
  "description": "string",
  "isPublic": true,
  "city": "string",
  "tags": ["string"],
  "meetingDurationMinutes": "number | null",
  "afterMeetingVenue": {
    "name": "string",
    "address": "string",
    "description": "string | undefined"
  } | null
}
```
**Response 201:** `Club`
**Errors:** 403 not organizer

---

### PATCH `/api/v1/clubs/:id/pause`
Поставити клуб на паузу.

**Response 200:** `Club`
**Errors:** 403, 404

---

### PATCH `/api/v1/clubs/:id/cancel`
Скасувати клуб. Встановлює `cancelledAt` і видаляє через 24 год.

**Response 200:** `Club`
**Errors:** 403, 404

---

### PATCH `/api/v1/clubs/:id/reschedule`
Перенести зустріч.

**Request body:**
```json
{ "newDate": "ISO date" }
```
**Response 200:** `Club`
**Errors:** 403, 404

---

### POST `/api/v1/clubs/:id/join`
Вступити до клубу.

**Response 200:** `{ memberCount: number }`
**Errors:** 403 banned, 404, 409 already member

---

### DELETE `/api/v1/clubs/:id/leave`
Покинути клуб.

**Response 204**
**Errors:** 404, 409 not a member

---

## 4. CLUB MEMBERS

### GET `/api/v1/clubs/:id/members`
Список учасників клубу з деталями.

**Response 200:**
```json
[
  {
    "userId": "string",
    "displayName": "string",
    "avatarUrl": "string | null",
    "role": "member" | "organizer",
    "socials": { ...UserSocials } | undefined,
    "socialsPublic": true
  }
]
```

---

### DELETE `/api/v1/clubs/:id/members/:userId`
Кікнути учасника (тільки організатор).

**Response 204**
**Errors:** 403, 404

---

### POST `/api/v1/clubs/:id/members/:userId/ban`
Забанити учасника.

**Request body:**
```json
{
  "duration": 1 | 3 | 5 | "permanent"
}
```
**Response 201:**
```json
{
  "userId": "string",
  "clubId": "string",
  "bannedAt": "ISO date",
  "duration": 1 | 3 | 5 | "permanent",
  "bannedBy": "string"
}
```
**Errors:** 403, 404

---

### GET `/api/v1/clubs/:id/bans`
Список банів для клубу.

**Response 200:** `BanRecord[]`
**Errors:** 403, 404

---

## 5. MEETINGS

### GET `/api/v1/clubs/:clubId/meetings`
Список зустрічей клубу (attendance history).

**Response 200:**
```json
[
  {
    "id": "string",
    "clubId": "string",
    "title": "string",
    "date": "ISO date",
    "attendees": ["userId"]
  }
]
```

---

## 6. QUIZZES

### GET `/api/v1/clubs/:clubId/quizzes`
Список квізів клубу.

**Response 200:**
```json
[
  {
    "id": "string",
    "clubId": "string",
    "createdBy": "string",
    "title": "string",
    "description": "string | null",
    "isActive": true
  }
]
```

---

### POST `/api/v1/clubs/:clubId/quizzes`
Створити квіз (тільки організатор).

**Request body:**
```json
{
  "title": "string",
  "description": "string"
}
```
**Response 201:** `Quiz`
**Errors:** 403

---

### GET `/api/v1/quizzes/:quizId/questions`
Питання квізу.

**Response 200:**
```json
[
  {
    "id": "string",
    "quizId": "string",
    "question": "string",
    "options": ["string"],
    "correctIndex": 1
  }
]
```
> **Увага:** `correctIndex` повернути тільки організатору; для учасника — не включати до відповіді, звіряти на беку.

---

### POST `/api/v1/quizzes/:quizId/questions`
Додати питання.

**Request body:**
```json
{
  "question": "string",
  "options": ["string", "string", "string", "string"],
  "correctIndex": 1
}
```
**Response 201:** `QuizQuestion`

---

### PATCH `/api/v1/quizzes/:quizId/active`
Вмикнути/вимкнути квіз.

**Request body:**
```json
{ "isActive": true }
```
**Response 200:** `Quiz`

---

### POST `/api/v1/quizzes/:quizId/attempts`
Відправити відповіді і отримати результат.

**Request body:**
```json
{
  "answers": [1, 0, 2]
}
```
**Response 201:**
```json
{
  "id": "string",
  "quizId": "string",
  "userId": "string",
  "score": 2,
  "total": 3,
  "answers": [1, 0, 2]
}
```
**Errors:** 403 quiz not active, 409 already attempted (optional)

---

## 7. RANDOMIZER

### GET `/api/v1/clubs/:clubId/randomizer/history`
Історія рандомайзер-сесій.

**Response 200:**
```json
[
  {
    "id": "string",
    "clubId": "string",
    "createdBy": "string",
    "purpose": "string",
    "candidates": [
      { "userId": "string", "displayName": "string", "avatarUrl": "string | null" }
    ],
    "result": { "userId": "string", "displayName": "string", "avatarUrl": "string | null" } | null,
    "createdAt": "ISO date"
  }
]
```

---

### POST `/api/v1/clubs/:clubId/randomizer/sessions`
Зберегти результат рандомайзер-сесії.

**Request body:**
```json
{
  "purpose": "string",
  "candidates": [{ "userId": "string", "displayName": "string", "avatarUrl": "string | null" }],
  "result": { "userId": "string", "displayName": "string", "avatarUrl": "string | null" } | null
}
```
**Response 201:** `RandomizerSession`

---

## 8. CHAT

> Чат зараз повністю мок. Для продакшну потрібно WebSocket (або SSE) + REST для завантаження.

### GET `/api/v1/clubs/:clubId/chat/rooms`
Кімнати чату клубу.

**Response 200:**
```json
[
  { "id": "string", "name": "string" }
]
```

---

### GET `/api/v1/chat/rooms/:roomId/messages`
Повідомлення кімнати (з пагінацією).

**Query params:** `?before=<messageId>&limit=50`

**Response 200:**
```json
[
  {
    "id": "string",
    "senderId": "string",
    "senderName": "string",
    "text": "string",
    "timestamp": "ISO date"
  }
]
```

---

### POST `/api/v1/chat/rooms/:roomId/messages`
Надіслати повідомлення (fallback без WebSocket).

**Request body:**
```json
{ "text": "string" }
```
**Response 201:** `ChatMessage`

---

### WebSocket `ws://host/api/v1/chat/rooms/:roomId`
Real-time повідомлення.

**Server → Client:**
```json
{
  "type": "message",
  "payload": { "id": "string", "senderId": "string", "senderName": "string", "text": "string", "timestamp": "ISO date" }
}
```
**Client → Server:**
```json
{ "text": "string" }
```

---

## Підсумкова таблиця

| # | Метод | Шлях | Опис |
|---|-------|------|------|
| 1 | POST | /auth/register | Реєстрація |
| 2 | POST | /auth/login | Вхід |
| 3 | POST | /auth/logout | Вихід |
| 4 | GET | /auth/me | Поточний юзер |
| 5 | GET | /users/me/stats | Статистика |
| 6 | PATCH | /users/me | Оновити профіль |
| 7 | PATCH | /users/me/role | Змінити роль |
| 8 | PATCH | /users/me/socials | Оновити соц. мережі |
| 9 | PATCH | /users/me/socials-visibility | Публічність соц. |
| 10 | GET | /clubs | Всі клуби |
| 11 | GET | /clubs/my | Мої клуби |
| 12 | GET | /clubs/:id | Деталі клубу |
| 13 | POST | /clubs | Створити клуб |
| 14 | PATCH | /clubs/:id/pause | Пауза клубу |
| 15 | PATCH | /clubs/:id/cancel | Скасувати клуб |
| 16 | PATCH | /clubs/:id/reschedule | Перенести зустріч |
| 17 | POST | /clubs/:id/join | Вступити |
| 18 | DELETE | /clubs/:id/leave | Покинути |
| 19 | GET | /clubs/:id/members | Учасники |
| 20 | DELETE | /clubs/:id/members/:userId | Кік |
| 21 | POST | /clubs/:id/members/:userId/ban | Бан |
| 22 | GET | /clubs/:id/bans | Список банів |
| 23 | GET | /clubs/:clubId/meetings | Зустрічі |
| 24 | GET | /clubs/:clubId/quizzes | Квізи |
| 25 | POST | /clubs/:clubId/quizzes | Створити квіз |
| 26 | GET | /quizzes/:quizId/questions | Питання |
| 27 | POST | /quizzes/:quizId/questions | Додати питання |
| 28 | PATCH | /quizzes/:quizId/active | Активація квізу |
| 29 | POST | /quizzes/:quizId/attempts | Відповіді |
| 30 | GET | /clubs/:clubId/randomizer/history | Історія |
| 31 | POST | /clubs/:clubId/randomizer/sessions | Зберегти сесію |
| 32 | GET | /clubs/:clubId/chat/rooms | Кімнати чату |
| 33 | GET | /chat/rooms/:roomId/messages | Повідомлення |
| 34 | POST | /chat/rooms/:roomId/messages | Надіслати |
| 35 | WS | /chat/rooms/:roomId | Real-time чат |
