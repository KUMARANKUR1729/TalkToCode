#ifndef PAYMENT_H
#define PAYMENT_H

#define MAX_PAYMENTS 16
#define PAYMENT_ERR_AMOUNT -1
#define PAYMENT_ERR_FULL -2

int payment_charge(const char *user_id, int amount_cents);
const char *payment_status(int payment_id);

#endif
