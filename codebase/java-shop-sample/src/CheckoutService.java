package com.example.shop;

import com.example.shop.model.Order;

public class CheckoutService {

    private final OrderRepository orders;
    private final InventoryService inventory;
    private final PaymentGateway payments;

    public CheckoutService(OrderRepository orders, InventoryService inventory, PaymentGateway payments) {
        this.orders = orders;
        this.inventory = inventory;
        this.payments = payments;
    }

    public String checkout(String orderId) {
        Order order = orders.findById(orderId);
        if (order == null) {
            throw new IllegalArgumentException("Order not found: " + orderId);
        }

        inventory.reserve(order.getItem(), order.getQuantity());
        String paymentId;
        try {
            paymentId = payments.charge(order.getUserId(), order.getTotalCents());
        } catch (PaymentException e) {
            inventory.release(order.getItem(), order.getQuantity());
            throw e;
        }

        orders.markPaid(orderId);
        return paymentId;
    }
}
