#include <string.h>
#include "inventory.h"

struct stock_entry {
    char item[32];
    int quantity;
};

static struct stock_entry stock[MAX_ITEMS];
static int stock_count = 0;

static struct stock_entry *stock_find(const char *item) {
    for (int i = 0; i < stock_count; i++) {
        if (strcmp(stock[i].item, item) == 0) {
            return &stock[i];
        }
    }
    return NULL;
}

void inventory_restock(const char *item, int quantity) {
    struct stock_entry *entry = stock_find(item);
    if (entry == NULL && stock_count < MAX_ITEMS) {
        entry = &stock[stock_count];
        stock_count++;
        strcpy(entry->item, item);
        entry->quantity = 0;
    }
    if (entry != NULL) {
        entry->quantity += quantity;
    }
}

int inventory_reserve(const char *item, int quantity) {
    struct stock_entry *entry = stock_find(item);
    if (entry == NULL || quantity <= 0 || entry->quantity < quantity) {
        return -1;
    }
    entry->quantity -= quantity;
    return 0;
}

void inventory_release(const char *item, int quantity) {
    inventory_restock(item, quantity);
}

int inventory_status(const char *item) {
    struct stock_entry *entry = stock_find(item);
    if (entry == NULL) {
        return -1;
    }
    return entry->quantity;
}
