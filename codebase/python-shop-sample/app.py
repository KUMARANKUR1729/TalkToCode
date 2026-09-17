from checkout_service import CheckoutService
from inventory import InventoryService
from models import Order
from order_repo import OrderRepository
from payment import PaymentGateway


def main():
    orders = OrderRepository()
    inventory = InventoryService({"keyboard": 5})
    payments = PaymentGateway()
    checkout = CheckoutService(orders, inventory, payments)

    order = Order("order-1", "user-1", "keyboard", 1, 4999)
    orders.save(order)
    payment_id = checkout.checkout(order.order_id)
    print(f"{order.status}: {payment_id}")


if __name__ == "__main__":
    main()
