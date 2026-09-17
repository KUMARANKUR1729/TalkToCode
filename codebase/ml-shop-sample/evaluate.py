class Evaluator:
    def __init__(self, model):
        self._model = model

    def score(self, matrix, labels):
        return self._model.score(matrix, labels)

    def report(self, matrix, labels):
        accuracy = self.score(matrix, labels)
        predicted_positive = sum(self._model.predict(features) for features in matrix)
        return {"accuracy": accuracy, "predicted_positive": predicted_positive}
