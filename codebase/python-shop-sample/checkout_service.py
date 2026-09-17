from inventory import InventoryService
from order_repo import OrderRepository
from payment import PaymentError, PaymentGateway


class CheckoutService:
    def __init__(
        self,
        orders: OrderRepository,
        inventory: InventoryService,
        payments: PaymentGateway,
    ):
        self._orders = orders
        self._inventory = inventory
        self._payments = payments

    def checkout(self, order_id: str):
        order = self._orders.find_by_id(order_id)
        if order is None:
            raise LookupError(f"Order not found: {order_id}")

        self._inventory.reserve(order.item, order.quantity)
        try:
            payment_id = self._payments.charge(order.user_id, order.total_cents)
        except PaymentError:
            self._inventory.release(order.item, order.quantity)
            raise

        self._orders.mark_paid(order_id)
        return payment_id
