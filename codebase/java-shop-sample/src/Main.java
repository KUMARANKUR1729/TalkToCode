package com.example.shop;

import com.example.shop.model.Order;

public class Main {

    public static void main(String[] args) {
        OrderRepository orders = new OrderRepository();
        InventoryService inventory = new InventoryService();
        PaymentGateway payments = new PaymentGateway();
        CheckoutService checkout = new CheckoutService(orders, inventory, payments);

        inventory.restock("keyboard", 5);
        Order order = new Order("order-1", "user-1", "keyboard", 1, 4999);
        orders.save(order);

        String paymentId = checkout.checkout(order.getOrderId());
        System.out.println(orders.status(order.getOrderId()) + ": " + paymentId
                + " stock=" + inventory.available("keyboard"));
    }
}
