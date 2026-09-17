import math


class ReturnRiskModel:
    def __init__(self, n_features: int, lr: float = 0.5):
        self.weights = [0.0] * n_features
        self.bias = 0.0
        self.lr = lr

    def predict_proba(self, features):
        z = self.bias
        for weight, value in zip(self.weights, features):
            z += weight * value
        return 1.0 / (1.0 + math.exp(-z))

    def predict(self, features):
        return 1 if self.predict_proba(features) >= 0.5 else 0

    def update(self, features, label: int):
        error = self.predict_proba(features) - label
        for index, value in enumerate(features):
            self.weights[index] -= self.lr * error * value
        self.bias -= self.lr * error
        return abs(error)

    def score(self, matrix, labels):
        if not labels:
            return 0.0
        correct = sum(
            1 for features, label in zip(matrix, labels) if self.predict(features) == label
        )
        return correct / len(labels)
