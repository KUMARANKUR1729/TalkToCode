from dataclasses import dataclass


@dataclass
class Order:
    order_id: str
    user_id: str
    item: str
    quantity: int
    total_cents: int
    status: str = "PENDING"
