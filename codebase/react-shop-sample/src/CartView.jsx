import React from "react";
import CheckoutButton from "./CheckoutButton";

export default function CartView({ items, total, onReload }) {
  if (items.length === 0) {
    return <p>Cart is empty.</p>;
  }

  return (
    <section>
      <ul>
        {items.map((item) => (
          <li key={item.sku}>
            {item.name} x {item.quantity}
          </li>
        ))}
      </ul>
      <p>Total: {total}</p>
      <button type="button" onClick={onReload}>
        Reload
      </button>
      <CheckoutButton items={items} />
    </section>
  );
}
