"""One adapter between shared temporal datasets and model-specific representations."""
from ..models.evaluation import evaluate_classifier


def model_partition(model, dataset, name: str):
    X, y = dataset.partition(name)
    length = getattr(model, 'sequence_length', 1)
    if length == 1 and not getattr(model, 'sequential', False):
        return X, y
    from .sequence_dataset import SequenceInput, valid_sequence_origins
    history = dataset.features.all_features
    mask = valid_sequence_origins(history, length)
    origins = X.index[mask.loc[X.index]]
    return SequenceInput(history, origins, length, X if name == 'train' else None), y.loc[origins]


from stock_app.research.registry_workflows import observed_fit


@observed_fit
def fit_partitioned(model, dataset):
    X, y = model_partition(model, dataset, 'train')
    if getattr(model, 'uses_validation', False):
        model.fit(X, y, validation=model_partition(model, dataset, 'validation'))
    else:
        model.fit(X, y)


def evaluate_partitioned(model, dataset, partition: str, returns, decision_threshold: float = .5):
    X, y = model_partition(model, dataset, partition)
    return evaluate_classifier(model, X, y, decision_threshold, future_returns=returns.loc[X.index])
