const BASE_URL = "/api";

export async function fetchCart() {
  const response = await fetch(`${BASE_URL}/cart`);
  if (!response.ok) {
    throw new Error("Unable to load cart");
  }
  return response.json();
}

export async function submitOrder(items) {
  const response = await fetch(`${BASE_URL}/orders`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ items }),
  });
  if (!response.ok) {
    throw new Error("Payment declined");
  }
  return response.json();
}

export async function orderStatus(orderId) {
  const response = await fetch(`${BASE_URL}/orders/${orderId}`);
  const data = await response.json();
  return data.status;
}
