package com.example.shop;

import java.util.HashMap;
import java.util.Map;

public class PaymentGateway {

    private final Map<String, String> payments = new HashMap<>();

    public String charge(String userId, int amountCents) {
        return charge(userId, amountCents, "CARD");
    }

    public String charge(String userId, int amountCents, String method) {
        if (amountCents <= 0) {
            throw new PaymentException("Payment amount must be positive");
        }
        String paymentId = "pay-" + (payments.size() + 1);
        payments.put(paymentId, "CAPTURED");
        return paymentId;
    }

    public String status(String paymentId) {
        return payments.getOrDefault(paymentId, "MISSING");
    }
}
