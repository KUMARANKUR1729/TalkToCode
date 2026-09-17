#ifndef CHECKOUT_H
#define CHECKOUT_H

#define CHECKOUT_ERR_NOT_FOUND -1
#define CHECKOUT_ERR_STOCK -2
#define CHECKOUT_ERR_PAYMENT -3

int checkout_process(const char *order_id);

#endif
