from fastapi import HTTPException, status

class NotFoundException(HTTPException):
    def __init__(self, detail: str = "Item not found"):
        super().__init__(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


class ProductNotFound(Exception):
    pass


class B2BServiceUnavailable(Exception):
    pass


class SubscriptionAlreadyExists(Exception):
    pass


class SKUNotFound(Exception):
    pass


class ProductUnavailable(Exception):
    pass


class CartItemNotFound(Exception):
    pass


class InsufficientStock(Exception):
    pass


class OrphanCategoryNode(Exception):
    pass


class AmbiguousBreadcrumbParams(Exception):
    pass


class MissingBreadcrumbParams(Exception):
    pass


class CategoryNotFound(Exception):
    pass


class B2BServiceError(Exception):
    def __init__(self, status_code: int, detail: str | dict):
        self.status_code = status_code
        self.detail = detail




