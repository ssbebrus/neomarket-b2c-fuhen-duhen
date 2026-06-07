# ADM-B2C-1: Управление статусами заказов — панель оператора

## Что и зачем

Реализован модуль оператора для ручного продвижения заказов по жизненному циклу. В учебном проекте нет WMS и курьерской системы, поэтому смена статусов — ручная, через REST API.

---

## ADR: Авторизация операторов

**Проблема:** endpoint'ы управления заказами должны быть закрыты от покупателей, но операторов может быть несколько — у каждого свои учётные данные.

**Рассмотренные варианты:**

| Вариант | Проблема |
|---|---|
| Статический `X-Admin-Key` | Один ключ на всех, нет аудита, утечка = компрометация всех |
| Поле `is_admin` в токене покупателя | Смешиваем домены, плохая изоляция |
| **Отдельная таблица `operators` + JWT** | ✅ Выбрано |

**Решение:** Отдельная таблица `operators` со своим паролем. Оператор логинится через `POST /api/v1/operator/auth/login` и получает JWT с `role=operator`. Dependency `get_current_operator` проверяет роль — покупательский токен возвращает `403 Forbidden`.

---

## State machine

Переходы — только вперёд по цепочке, перескочить и вернуть назад нельзя:

```
PAID → ASSEMBLING → DELIVERING → DELIVERED
```

Терминальные статусы (`DELIVERED`, `CANCELLED`) и `CANCEL_PENDING` заблокированы для смены.

> ⚠️ **Примечание:** вызов `POST /api/v1/inventory/fulfill` к B2B при переходе в `DELIVERED` будет реализован в следующем PR (US-ORD-05), чтобы не смешивать фичи в одном PR.

---

## Новые endpoint'ы

Все endpoint'ы требуют `Authorization: Bearer <operator_token>`.

| Метод | Путь | Описание |
|---|---|---|
| `POST` | `/api/v1/operator/auth/login` | Вход оператора, возвращает JWT |
| `GET` | `/api/v1/operator/orders` | Список всех заказов (фильтры: `status`, `user_id`, `date_from`, `date_to`) |
| `GET` | `/api/v1/operator/orders/{order_id}` | Детали заказа |
| `POST` | `/api/v1/operator/orders/{order_id}/advance` | Продвинуть статус на один шаг |
| `POST` | `/api/v1/operator/orders/{order_id}/cancel` | Отменить заказ (PAID / ASSEMBLING) |

### Примеры запросов

```bash
# Логин
POST /api/v1/operator/auth/login
{"email": "operator@company.com", "password": "..."}
→ {"access_token": "...", "token_type": "bearer"}

# Продвинуть статус
POST /api/v1/operator/orders/{id}/advance
Authorization: Bearer <token>
→ {"status": "ASSEMBLING", ...}

# Отмена оператором
POST /api/v1/operator/orders/{id}/cancel
Authorization: Bearer <token>
{"reason": "Покупатель отказался"}
```

### Логика отмены оператором

При отмене (`PAID` / `ASSEMBLING` → `CANCELLED`) вызывается `POST /api/v1/inventory/unreserve` к B2B. Если B2B недоступен — заказ переходит в `CANCEL_PENDING`, откуда его заберёт уже существующий фоновый воркер.

---

## Изменённые файлы

### Новые файлы

- `src/modules/operator/models.py` — модель `Operator` (id, email, hashed_password, full_name)
- `src/modules/operator/schemas.py` — `OperatorLoginRequest`, `OperatorTokenResponse`
- `src/modules/operator/service.py` — auth (bcrypt + JWT), state machine, cancel, list
- `src/modules/operator/auth.py` — dependency `get_current_operator`
- `src/modules/operator/router.py` — все 5 endpoint'ов
- `src/db/migrations/versions/01c94d0a6b1f_create_operators_table.py` — миграция

### Изменённые файлы

- `src/db/__init__.py` — регистрация модели `Operator`
- `src/api/router.py` — подключение `operator_router`
- `src/core/exceptions.py` — добавлено `OrderAdvanceNotAllowed`
- `pytest.ini` — добавлен `filterwarnings` для подавления deprecation от passlib

---

## Тесты

```
tests/test_operator.py — 19 сценариев:

Auth:
  ✅ login success → JWT с role=operator
  ✅ wrong password → 401
  ✅ unknown email → 401
  ✅ no token → 401
  ✅ buyer token → 403

List / Detail:
  ✅ operator sees all orders (не только свои)
  ✅ filter by status
  ✅ get order detail
  ✅ not found → 404

State machine advance:
  ✅ PAID → ASSEMBLING
  ✅ ASSEMBLING → DELIVERING
  ✅ DELIVERING → DELIVERED (delivered_at заполнен)
  ✅ terminal status (DELIVERED) → 409
  ✅ CANCEL_PENDING → 409
  ✅ CANCELLED → 409

Operator cancel:
  ✅ PAID → CANCELLED (B2B OK)
  ✅ ASSEMBLING → CANCELLED (B2B OK)
  ✅ DELIVERING → 409 (нельзя отменить)
  ✅ B2B unreserve fail → CANCEL_PENDING

Итог: 57 passed, 0 failed (полная регрессия)
```

---

## Как проверить вручную

```bash
# 1. Применить миграцию (уже применена в dev-окружении)
docker compose exec backend alembic upgrade head

# 2. Создать оператора (через скрипт или напрямую в БД)
# INSERT INTO operators (email, hashed_password, full_name)
# VALUES ('op@company.com', '<bcrypt_hash>', 'Test Operator');

# 3. Залогиниться
curl -X POST http://localhost:8002/api/v1/operator/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "op@company.com", "password": "yourpassword"}'

# 4. Продвинуть заказ
curl -X POST http://localhost:8002/api/v1/operator/orders/{ORDER_ID}/advance \
  -H "Authorization: Bearer <token>"
```
