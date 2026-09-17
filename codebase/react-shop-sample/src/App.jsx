import React from "react";
import CartView from "./CartView";
import { useCart } from "./useCart";

export default function App() {
  const { items, total, error, reload } = useCart();

  return (
    <main>
      <h1>Shop</h1>
      {error ? <p role="alert">{error}</p> : null}
      <CartView items={items} total={total} onReload={reload} />
    </main>
  );
}
