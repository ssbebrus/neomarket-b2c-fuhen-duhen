from src.db.base import Base
from src.modules.favorites.models import Favorite, ProductSubscription
from src.modules.cart.models import CartItem

# Импортируем все модели сюда, чтобы метаданные Base загрузились для Alembic
# Иначе alembic --autogenerate не сможет найти таблицы
