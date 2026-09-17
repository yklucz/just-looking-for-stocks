"""Dashboard requests create durable candidates; they never replace bindings."""
from flask import current_app, has_app_context
from .artifact_loader import load_bound_model
from .schemas import PredictionError


def request_training(ticker: str, task: str = 'binary') -> dict:
    from ..research.runtime import get_runtime
    if task not in {'binary', 'regression'}:
        raise PredictionError('INVALID_REQUEST', 'Unsupported training task', 400)
    ticker = ticker.upper()
    runtime = get_runtime()
    if runtime.store.active(ticker, task):
        return {'status': 'ready', 'ticker': ticker}
    try:
        load_bound_model(ticker, 'xgboost' if task == 'binary' else 'xgboost_regressor', task=task)
        return {'status': 'ready', 'ticker': ticker}
    except PredictionError as exc:
        if exc.code != 'MODEL_NOT_AVAILABLE':
            raise
    models = [item for item in runtime.store.list('models', symbol=ticker)
              if item['task'] == task and item['state'] in {'candidate', 'shadow'}]
    if models:
        return {'status': 'candidate', 'ticker': ticker, 'model_id': models[0]['id'],
                'message': 'Candidate ready for research review; no active model has been replaced.'}
    jobs = [job for job in runtime.store.list('jobs', symbol=ticker)
            if job['kind'] == 'experiment' and job['parameters']['task'] == task]
    if jobs and jobs[0]['state'] in {'failed', 'cancelled', 'paused'}:
        return {'status': jobs[0]['state'], 'ticker': ticker, 'job_id': jobs[0]['id'],
                'error': jobs[0].get('error') or 'Open Research to review and resume this job.'}
    job = runtime.submit('experiment', ticker, task)
    if has_app_context() and current_app.config.get('RESEARCH_BACKGROUND', not current_app.testing):
        runtime.start()
    return {'status': 'training', 'ticker': ticker, 'job_id': job['id'], 'state': job['state']}
