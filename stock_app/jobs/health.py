"""Read-only operational health derived from persisted source timestamps."""
from dataclasses import dataclass

import pandas as pd

from stock_app.research.calendar import latest_completed_session, sessions_between, utc_timestamp


@dataclass(frozen=True)
class FreshnessPolicy:
    warning_sessions: int = 1
    stale_sessions: int = 2
    warning_hours: int = 72
    stale_hours: int = 168


def source_freshness(data, symbol, now, policy=FreshnessPolicy()):
    clock = utc_timestamp(now)
    result = {'symbol': symbol, 'status': 'unknown', 'reason': 'No persisted source timestamps'}
    try:
        meta = data.latest(symbol, now=clock)
        result['snapshot_id'] = meta.get('id')
        if meta.get('status') != 'valid':
            return {**result, 'reason': 'No checksum-valid source snapshot'}
        if not meta.get('created_at') or not meta.get('last_session'):
            return result
        observed = utc_timestamp(meta['created_at'])
        last = utc_timestamp(meta['last_session'])
        expected = latest_completed_session(clock - pd.Timedelta(minutes=30), meta.get('exchange', 'XNYS'))
        if observed > clock or last > latest_completed_session(clock, meta.get('exchange', 'XNYS')):
            return {**result, 'reason': 'Source timestamps are in the future'}
        lag = max(0, len(sessions_between(last, expected, meta.get('exchange', 'XNYS'))) - 1) if last <= expected else 0
        age_hours = (clock - observed).total_seconds() / 3600
        state = 'fresh'
        if lag >= policy.stale_sessions or age_hours >= policy.stale_hours:
            state = 'stale'
        elif (lag >= policy.warning_sessions or age_hours >= policy.warning_hours
              or (meta.get('latest_attempt') and (meta['latest_attempt'].get('id') != meta['id']
                  or meta['latest_attempt'].get('status') != 'valid'))):
            state = 'warning'
        return {**result, 'status': state, 'source_at': observed.isoformat(),
                'last_session': last.date().isoformat(), 'expected_session': expected.date().isoformat(),
                'lag_sessions': lag, 'age_hours': round(age_hours, 2),
                'reason': ('Source meets session and observation-age policy' if state == 'fresh'
                           else 'Source is behind policy or a newer refresh failed; downstream jobs require fresh inputs')}
    except (KeyError, ValueError, TypeError, OSError, OverflowError):
        return {**result, 'reason': 'Source metadata is missing, invalid, unreadable, or unverifiable'}


def job_health(runner, *, heartbeat_warning_minutes=30):
    now = runner.now()
    definitions = runner.definitions
    sources = [source_freshness(runner.runtime.data, symbol, now)
               for symbol in sorted({job.symbol for job in definitions.values()})]
    heartbeat = runner.store.heartbeat()
    with runner.runtime.worker_lease() as owned:
        worker_active = not owned
    warnings = []
    if heartbeat is None:
        warnings.append('Runner has never reported a heartbeat')
    elif (now - utc_timestamp(heartbeat['at'])).total_seconds() > heartbeat_warning_minutes * 60:
        warnings.append('Runner heartbeat is overdue' + ('; worker lock is still held' if worker_active else ''))
    saved = {row['name']: row for row in runner.store.schedules()}
    for name, job in definitions.items():
        if name not in saved or utc_timestamp(saved[name]['last_scheduled_at']) < job.latest(now):
            warnings.append(f'{name}: scheduled execution is due or missed')
    runs = runner.store.runs()
    for run in runs:
        if run['status'] in {'failed', 'blocked', 'retry'}:
            warnings.append(f"{run['name']}: {run['status']} ({run['id']})")
        elif run['status'] == 'running' and not worker_active:
            warnings.append(f"{run['name']}: interrupted worker; run-due must recover")
        elif run['status'] == 'queued' and utc_timestamp(run['next_attempt_at']) < now:
            warnings.append(f"{run['name']}: queued execution is overdue")
    for source in sources:
        if source['status'] != 'fresh':
            warnings.append(f"{source['symbol']}: {source['status']} — {source['reason']}")
    return {'status': 'warning' if warnings else 'healthy', 'checked_at': now.isoformat(),
            'heartbeat': heartbeat, 'worker_active': worker_active, 'sources': sources, 'warnings': warnings}
