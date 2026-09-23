"""Compose existing audits with read-only evidence and reconciliation findings."""
from collections import Counter
from .integrity import reader, rows, verify_run
from .integrity_reconcile import findings, list_cases
from .integrity_replay import reproduction_history
from .store import utcnow


def audit_integrity(path, *, depth='metadata', clock=utcnow):
    from .registry_admin import audit
    from .forecast_audit import audit_forecasts
    if depth not in {'metadata','artifact'}:
        raise ValueError('Read-only audit supports metadata/artifact; use explicit CLI --replay for reproduction writes')
    registry = audit(path, verify_files=depth == 'artifact')
    if registry['status'] == 'migration_required':
        return {'status':'migration_required','registry':registry}
    with reader(path) as db:
        runs = rows(db, 'research_runs')
    reviews = [verify_run(path, r['id'], depth, clock=clock) for r in runs]
    evidence = Counter()
    for review in reviews:
        evidence.update(review['counts'])
    detected = findings(path, depth=depth, clock=clock)
    forecast = audit_forecasts(path)
    cases = list_cases(path)
    return {'status': 'attention' if detected or registry['status'] != 'ok' or forecast['status'] not in {'healthy','empty'} else 'ok',
            'depth':depth, 'registry':registry, 'forecast_ledger':forecast, 'evidence':dict(evidence),
            'reproducibility':dict(Counter(r['status'] for r in reproduction_history(path))),
            'reconciliation':dict(Counter(c['state'] for c in cases)),
            'integrity_findings':dict(Counter(f['category'] for f in detected)), 'findings':detected,
            'reviews':reviews, 'replay_performed':False}
