class OutOfStockError(Exception):
    pass


class InventoryService:
    def __init__(self, stock):
        self._stock = dict(stock)

    def reserve(self, item: str, quantity: int):
        available = self._stock.get(item, 0)
        if quantity <= 0 or available < quantity:
            raise OutOfStockError(f"Cannot reserve {quantity} x {item}")
        self._stock[item] = available - quantity

    def release(self, item: str, quantity: int):
        self._stock[item] = self._stock.get(item, 0) + quantity
