#include <string.h>
#include "payment.h"

static char payment_states[MAX_PAYMENTS][16];
static int payment_count = 0;

int payment_charge(const char *user_id, int amount_cents) {
    if (amount_cents <= 0) {
        return PAYMENT_ERR_AMOUNT;
    }
    if (payment_count >= MAX_PAYMENTS) {
        return PAYMENT_ERR_FULL;
    }
    int payment_id = payment_count;
    payment_count++;
    strcpy(payment_states[payment_id], "CAPTURED");
    return payment_id;
}

const char *payment_status(int payment_id) {
    if (payment_id < 0 || payment_id >= payment_count) {
        return "MISSING";
    }
    return payment_states[payment_id];
}
