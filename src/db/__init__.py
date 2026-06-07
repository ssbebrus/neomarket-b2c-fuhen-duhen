from src.db.base import Base
from src.modules.favorites.models import Favorite, ProductSubscription
from src.modules.cart.models import CartItem, EventIdempotencyKey
from src.modules.banners.models import Banner, BannerEvent
from src.modules.catalog.models import Collection, CollectionProduct
from src.modules.orders.models import Order, OrderItem



# Импортируем все модели сюда, чтобы метаданные Base загрузились для Alembic
# Иначе alembic --autogenerate не сможет найти таблицы

