#ifndef ORDER_H
#define ORDER_H

#define MAX_ORDERS 16

struct order {
    char order_id[16];
    char user_id[16];
    char item[32];
    int quantity;
    int total_cents;
    char status[16];
};

#endif
