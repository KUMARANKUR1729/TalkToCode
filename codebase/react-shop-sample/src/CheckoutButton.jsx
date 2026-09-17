import React from "react";
import { useCheckout } from "./useCheckout";

export default function CheckoutButton({ items }) {
  const { status, error, submit } = useCheckout();

  return (
    <div>
      <button
        type="button"
        disabled={status === "PENDING"}
        onClick={() => submit(items)}
      >
        {status === "PENDING" ? "Placing order..." : "Checkout"}
      </button>
      {error ? <span role="alert">{error}</span> : null}
      {status === "PAID" ? <span>Order placed</span> : null}
    </div>
  );
}
