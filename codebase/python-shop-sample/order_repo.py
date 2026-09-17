from models import Order


class OrderRepository:
    def __init__(self):
        self._orders = {}

    def save(self, order: Order):
        self._orders[order.order_id] = order

    def find_by_id(self, order_id: str):
        return self._orders.get(order_id)

    def mark_paid(self, order_id: str):
        order = self.find_by_id(order_id)
        if order is None:
            raise LookupError(f"Order not found: {order_id}")
        order.status = "PAID"

    def status(self, order_id: str):
        order = self.find_by_id(order_id)
        return order.status if order else "MISSING"
