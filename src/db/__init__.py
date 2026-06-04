from src.db.base import Base
from src.modules.favorites.models import Favorite, ProductSubscription
from src.modules.cart.models import CartItem
from src.modules.banners.models import Banner, BannerEvent

# Импортируем все модели сюда, чтобы метаданные Base загрузились для Alembic
# Иначе alembic --autogenerate не сможет найти таблицы

