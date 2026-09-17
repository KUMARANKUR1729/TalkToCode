def normalize_amount(amount_cents: int):
    return amount_cents / 10000.0


def build_features(row):
    return [normalize_amount(row["amount_cents"]), float(row["items"])]


def build_matrix(dataset):
    return [build_features(row) for row in dataset.rows()]
