SAMPLE_ORDERS = [
    {"amount_cents": 1500, "items": 1, "returned": 0},
    {"amount_cents": 2200, "items": 1, "returned": 0},
    {"amount_cents": 3100, "items": 2, "returned": 0},
    {"amount_cents": 8200, "items": 4, "returned": 1},
    {"amount_cents": 9600, "items": 5, "returned": 1},
    {"amount_cents": 12000, "items": 6, "returned": 1},
]


class OrderDataset:
    def __init__(self, rows):
        self._rows = list(rows)

    def __len__(self):
        return len(self._rows)

    def rows(self):
        return list(self._rows)

    def labels(self):
        return [row["returned"] for row in self._rows]
