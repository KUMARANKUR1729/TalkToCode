class PaymentError(Exception):
    pass


class PaymentGateway:
    def __init__(self):
        self._payments = {}

    def charge(self, user_id: str, amount_cents: int):
        if amount_cents <= 0:
            raise PaymentError("Payment amount must be positive")
        payment_id = f"pay-{len(self._payments) + 1}"
        self._payments[payment_id] = "CAPTURED"
        return payment_id

    def status(self, payment_id: str):
        return self._payments.get(payment_id, "MISSING")
