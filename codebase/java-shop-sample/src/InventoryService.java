package com.example.shop;

import java.util.HashMap;
import java.util.Map;

public class InventoryService {

    private final Map<String, Integer> stock = new HashMap<>();

    public void restock(String item, int quantity) {
        stock.merge(item, quantity, Integer::sum);
    }

    public void reserve(String item, int quantity) {
        int available = stock.getOrDefault(item, 0);
        if (quantity <= 0 || available < quantity) {
            throw new OutOfStockException("Cannot reserve " + quantity + " x " + item);
        }
        stock.put(item, available - quantity);
    }

    public void release(String item, int quantity) {
        restock(item, quantity);
    }

    public int available(String item) {
        return stock.getOrDefault(item, 0);
    }
}
