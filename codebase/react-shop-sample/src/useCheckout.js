import { useCallback, useState } from "react";
import { orderStatus, submitOrder } from "./shopApi";

function formatError(err) {
  return `Checkout failed: ${err.message}`;
}

export function useCheckout() {
  const [status, setStatus] = useState("IDLE");
  const [error, setError] = useState(null);

  const submit = useCallback(async (items) => {
    setStatus("PENDING");
    try {
      const order = await submitOrder(items);
      const nextStatus = await orderStatus(order.orderId);
      setStatus(nextStatus);
      setError(null);
    } catch (err) {
      setStatus("FAILED");
      setError(formatError(err));
    }
  }, []);

  return { status, error, submit };
}
