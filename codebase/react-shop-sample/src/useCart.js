import { useCallback, useEffect, useState } from "react";
import { fetchCart } from "./shopApi";

function formatError(err) {
  return `Cart error: ${err.message}`;
}

export function useCart() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(null);

  const reload = useCallback(async () => {
    try {
      const cart = await fetchCart();
      setItems(cart.items);
      setTotal(cart.total);
      setError(null);
    } catch (err) {
      setError(formatError(err));
    }
  }, []);

  useEffect(() => {
    reload();
  }, [reload]);

  return { items, total, error, reload };
}
