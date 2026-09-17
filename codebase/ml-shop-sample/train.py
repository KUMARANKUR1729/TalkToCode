from dataset import SAMPLE_ORDERS, OrderDataset
from evaluate import Evaluator
from features import build_matrix
from model import ReturnRiskModel


def train_epoch(model, matrix, labels):
    total_error = 0.0
    for features, label in zip(matrix, labels):
        total_error += model.update(features, label)
    return total_error / len(labels)


def train_model(epochs: int = 200):
    dataset = OrderDataset(SAMPLE_ORDERS)
    matrix = build_matrix(dataset)
    labels = dataset.labels()
    model = ReturnRiskModel(n_features=len(matrix[0]))

    for _ in range(epochs):
        train_epoch(model, matrix, labels)

    return model, matrix, labels


def main():
    model, matrix, labels = train_model()
    evaluator = Evaluator(model)
    report = evaluator.report(matrix, labels)
    print(f"accuracy={report['accuracy']:.2f} positives={report['predicted_positive']}")


if __name__ == "__main__":
    main()
