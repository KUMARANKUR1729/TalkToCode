package com.example.shop.model;

public class Order {

    private final String orderId;
    private final String userId;
    private final String item;
    private final int quantity;
    private final int totalCents;
    private String status = "PENDING";

    public Order(String orderId, String userId, String item, int quantity, int totalCents) {
        this.orderId = orderId;
        this.userId = userId;
        this.item = item;
        this.quantity = quantity;
        this.totalCents = totalCents;
    }

    public String getOrderId() {
        return orderId;
    }

    public String getUserId() {
        return userId;
    }

    public String getItem() {
        return item;
    }

    public int getQuantity() {
        return quantity;
    }

    public int getTotalCents() {
        return totalCents;
    }

    public String getStatus() {
        return status;
    }

    public void markPaid() {
        this.status = "PAID";
    }
}
