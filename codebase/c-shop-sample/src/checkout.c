#include "checkout.h"
#include "inventory.h"
#include "order_repo.h"
#include "payment.h"

int checkout_process(const char *order_id) {
    struct order *o = order_repo_find_by_id(order_id);
    if (o == NULL) {
        return CHECKOUT_ERR_NOT_FOUND;
    }
    if (inventory_reserve(o->item, o->quantity) != 0) {
        return CHECKOUT_ERR_STOCK;
    }

    int payment_id = payment_charge(o->user_id, o->total_cents);
    if (payment_id < 0) {
        inventory_release(o->item, o->quantity);
        return CHECKOUT_ERR_PAYMENT;
    }

    order_repo_mark_paid(order_id);
    return payment_id;
}
