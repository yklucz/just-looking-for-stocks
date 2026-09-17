"""Exchange sessions and actual closes, including holidays and early closes."""
from functools import lru_cache
import pandas as pd
import exchange_calendars


def utc_timestamp(value=None):
    stamp = pd.Timestamp.now(tz='UTC') if value is None else pd.Timestamp(value)
    if pd.isna(stamp):
        raise ValueError('Timestamp cannot be missing')
    return stamp.tz_localize('UTC') if stamp.tzinfo is None else stamp.tz_convert('UTC')


def session_index(values):
    # Yahoo daily index is midnight in the exchange timezone. Preserve its date.
    index = pd.DatetimeIndex(values)
    if index.hasnans:
        raise ValueError('Session dates cannot be missing')
    if index.tz is not None:
        index = index.tz_localize(None)
    return index.normalize().tz_localize('UTC')


@lru_cache(maxsize=16)
def _calendar(exchange, start_year, end_year):
    return exchange_calendars.get_calendar(exchange, start=f'{start_year}-01-01', end=f'{end_year}-12-31')


def session_close(session, exchange='XNYS'):
    day = session_index([session])[0]
    calendar = _calendar(exchange, day.year-1, day.year+1)
    return utc_timestamp(calendar.session_close(day.tz_localize(None)))


def sessions_between(start, end, exchange='XNYS'):
    first, last = session_index([start, end])
    calendar = _calendar(exchange, first.year-1, last.year+1)
    return session_index(calendar.sessions_in_range(first.tz_localize(None), last.tz_localize(None)))


def latest_completed_session(now=None, exchange='XNYS'):
    timestamp = utc_timestamp(now)
    day = timestamp.normalize()
    sessions = sessions_between(day - pd.Timedelta(days=14), day, exchange)
    for session in reversed(sessions):
        if session_close(session, exchange) <= timestamp:
            return session
    raise ValueError('No recently completed exchange session')
