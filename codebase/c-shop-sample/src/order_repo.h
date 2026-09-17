#ifndef ORDER_REPO_H
#define ORDER_REPO_H

#include "order.h"

int order_repo_save(const struct order *o);
struct order *order_repo_find_by_id(const char *order_id);
int order_repo_mark_paid(const char *order_id);
const char *order_repo_status(const char *order_id);

#endif
