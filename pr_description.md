# ADM-B2C-1 & US-ORD-05: Управление статусами заказов и списание резерва

## Что и зачем

1. Реализован модуль оператора для ручного продвижения заказов по жизненному циклу (PAID -> ASSEMBLING -> DELIVERING -> DELIVERED).
2. Реализована фича **US-ORD-05: финальное списание резерва при доставке**. При переводе заказа оператором в статус `DELIVERED` сервис делает запрос к B2B для списания остатков товара со склада. Если B2B недоступен, заказ остается в статусе `DELIVERED`, а повторные попытки списания осуществляются асинхронно фоновым воркером до тех пор, пока B2B не подтвердит операцию.

---

## ADR: Способ триггера списания (fulfill) при переходе в DELIVERED

Для вызова списания резервов в B2B при переходе заказа в статус `DELIVERED` были рассмотрены следующие варианты:
1. **Эквивалент post_save сигнала (SQLAlchemy `after_update` event listener):** Автоматический вызов при изменении поля `status` в модели `Order` на уровне ORM.
2. **Эквивалент Django Admin action (вызов в router / API handler):** Вызов B2B API непосредственно в эндпоинте продвижения заказа.
3. **Эквивалент overriding save() в модели (вызов внутри бизнес-логики сервиса):** Вызов B2B API внутри метода `OperatorService.advance_status`.

**Выбран вариант 3 (вызов в методе сервиса).**

**Критерии выбора:**
- **Риск случайного двойного вызова:** Сигналы/event-листенеры (вариант 1) несут высокий риск повторного вызова B2B API при любых сторонних обновлениях записи заказа в БД (например, при обновлении других полей). Вызов в сервисе (вариант 3) строго привязан к конкретному переходу бизнес-логики.
- **Возможность тестирования без Django Admin:** Вариант 2 требует прохождения через HTTP-слой роутера или интерфейс админки. Вариант 3 позволяет легко покрыть логику чистыми unit-тестами сервиса, изолированно мокируя B2B-клиент.

---

## Как это работает (State Machine & Async Retry)

- Переходы статусов происходят строго вперед:
  ```
  PAID → ASSEMBLING → DELIVERING → DELIVERED
  ```
- При переходе в `DELIVERED` выполняется попытка списания: `POST /api/v1/inventory/fulfill` на B2B.
- Если B2B доступен: резерв списывается, флаг `b2b_fulfilled` в БД выставляется в `True`.
- Если B2B падает / недоступен:
  - Заказ **остается в статусе `DELIVERED`** (покупатель уже получил товар, откатывать назад нельзя).
  - Флаг `b2b_fulfilled` остается `False`.
  - Фоновый воркер `run_fulfill_worker` периодически опрашивает заказы с `status == 'DELIVERED'` и `b2b_fulfilled == False` и осуществляет повторные попытки списания резервов до успеха.

---

## Изменения в коде

- `src/modules/orders/models.py` — Добавлено поле `b2b_fulfilled` (Boolean, default=False).
- `src/db/migrations/versions/2026_06_07_1325-d0c6e0675dc8_add_b2b_fulfilled_to_orders.py` — Alembic-миграция.
- `src/modules/operator/service.py` — Добавлен вызов `POST /api/v1/inventory/fulfill` в `advance_status` при переходе в `DELIVERED`.
- `src/modules/orders/service.py` — Реализованы `process_fulfill_pending` и `run_fulfill_worker` для фонового ретрая.
- `src/main.py` — Регистрация фонового воркера `run_fulfill_worker` в `lifespan` приложения.
- `tests/test_operator_fulfill.py` — Новые unit-тесты для US-ORD-05.

---

## Результаты тестирования

Лог выполнения тестов списания резерва (`test_operator_fulfill.py`):
```
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.0.3, pluggy-1.6.0 -- /usr/local/bin/python3.12
cachedir: .pytest_cache
rootdir: /app
configfile: pytest.ini
plugins: asyncio-1.3.0, anyio-4.12.1
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=session, asyncio_default_test_loop_scope=session
collecting ... collected 3 items

tests/test_operator_fulfill.py::test_delivered_status_triggers_fulfill_to_b2b PASSED [ 33%]
tests/test_operator_fulfill.py::test_fulfill_failure_retried_asynchronously PASSED [ 66%]
tests/test_operator_fulfill.py::test_repeated_fulfill_idempotent PASSED  [100%]

============================== 3 passed in 0.85s ===============================
```

Общий лог регрессионных тестов (60 пройденных тестов):
```
tests/test_b2b_events.py PASSED
tests/test_banners.py PASSED
tests/test_cart.py PASSED
tests/test_favorites.py PASSED
tests/test_operator.py PASSED
tests/test_operator_fulfill.py PASSED
tests/test_orders.py PASSED

============================== 60 passed in 5.92s ==============================
```

---

## Как проверить вручную

1. Применить миграции:
   ```bash
   docker compose exec backend alembic upgrade head
   ```
2. Перевести заказ из статуса `DELIVERING` в статус `DELIVERED` через API оператора:
   ```bash
   curl -X POST http://localhost:8002/api/v1/operator/orders/{ORDER_ID}/advance \
     -H "Authorization: Bearer <operator_token>"
   ```
3. Проверить в БД или логах, что для заказа флаг `b2b_fulfilled` установлен в `True`.
