#include <string.h>
#include "order_repo.h"

static struct order orders[MAX_ORDERS];
static int order_count = 0;

int order_repo_save(const struct order *o) {
    if (order_count >= MAX_ORDERS) {
        return -1;
    }
    orders[order_count] = *o;
    order_count++;
    return 0;
}

struct order *order_repo_find_by_id(const char *order_id) {
    for (int i = 0; i < order_count; i++) {
        if (strcmp(orders[i].order_id, order_id) == 0) {
            return &orders[i];
        }
    }
    return NULL;
}

int order_repo_mark_paid(const char *order_id) {
    struct order *o = order_repo_find_by_id(order_id);
    if (o == NULL) {
        return -1;
    }
    strcpy(o->status, "PAID");
    return 0;
}

const char *order_repo_status(const char *order_id) {
    struct order *o = order_repo_find_by_id(order_id);
    if (o == NULL) {
        return "MISSING";
    }
    return o->status;
}
