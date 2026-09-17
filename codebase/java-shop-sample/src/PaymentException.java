package com.example.shop;

public class PaymentException extends RuntimeException {

    public PaymentException(String message) {
        super(message);
    }
}
