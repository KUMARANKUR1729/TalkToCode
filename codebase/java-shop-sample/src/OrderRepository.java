package com.example.shop;

import com.example.shop.model.Order;
import java.util.HashMap;
import java.util.Map;

public class OrderRepository {

    private final Map<String, Order> orders = new HashMap<>();

    public void save(Order order) {
        orders.put(order.getOrderId(), order);
    }

    public Order findById(String orderId) {
        return orders.get(orderId);
    }

    public void markPaid(String orderId) {
        Order order = findById(orderId);
        if (order == null) {
            throw new IllegalArgumentException("Order not found: " + orderId);
        }
        order.markPaid();
    }

    public String status(String orderId) {
        Order order = findById(orderId);
        if (order == null) {
            return "MISSING";
        }
        return order.getStatus();
    }
}
