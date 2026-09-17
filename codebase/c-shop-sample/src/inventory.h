#ifndef INVENTORY_H
#define INVENTORY_H

#define MAX_ITEMS 8

void inventory_restock(const char *item, int quantity);
int inventory_reserve(const char *item, int quantity);
void inventory_release(const char *item, int quantity);
int inventory_status(const char *item);

#endif
