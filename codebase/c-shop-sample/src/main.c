#include <stdio.h>
#include <string.h>
#include "checkout.h"
#include "inventory.h"
#include "order_repo.h"

int main(void) {
    struct order o;
    strcpy(o.order_id, "order-1");
    strcpy(o.user_id, "user-1");
    strcpy(o.item, "keyboard");
    o.quantity = 1;
    o.total_cents = 4999;
    strcpy(o.status, "PENDING");

    inventory_restock("keyboard", 5);
    order_repo_save(&o);

    int payment_id = checkout_process(o.order_id);
    printf("%s: payment=%d stock=%d\n", order_repo_status(o.order_id),
           payment_id, inventory_status("keyboard"));
    return 0;
}
