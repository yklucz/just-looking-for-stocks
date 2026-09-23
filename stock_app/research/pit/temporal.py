"""Precision-preserving availability policies; never assume a timezone."""
from dataclasses import dataclass, asdict
from datetime import date, datetime, time, timedelta, timezone
from enum import StrEnum
from zoneinfo import ZoneInfo


class Strictness(StrEnum):
    STRICT = 'strict'
    ALLOW_PROXY = 'allow_proxy'
    OBSERVED_ONLY = 'observed_only'


def instant(value):
    if not isinstance(value,str):
        raise ValueError('Timezone-aware ISO timestamp required')
    stamp = datetime.fromisoformat(value.replace('Z','+00:00'))
    if stamp.tzinfo is None:
        raise ValueError('Explicit timezone required; dates are not exact timestamps')
    return stamp.astimezone(timezone.utc).isoformat()


def day(value):
    if not isinstance(value,str) or len(value)!=10:
        raise ValueError('ISO calendar date required')
    return date.fromisoformat(value).isoformat()


def temporal_value(value):
    if value is None:
        return None
    if not isinstance(value,dict) or set(value)!={'precision','value'}:
        raise ValueError('Temporal value requires precision and value')
    if value['precision'] not in {'date','instant'}:
        raise ValueError('Temporal precision must be date or instant')
    return {'precision':value['precision'],'value':day(value['value']) if value['precision']=='date' else instant(value['value'])}


@dataclass(frozen=True)
class Availability:
    kind: str
    basis: str
    value: str | None = None
    timezone: str | None = None
    policy: str | None = None

    def document(self):
        if self.kind not in {'exact','date_level','proxy','observed_only','unknown'} or not self.basis.strip():
            raise ValueError('Typed availability and nonempty basis required')
        value=asdict(self)
        if self.kind in {'exact','proxy'}:
            value['value']=instant(self.value)
            if self.timezone is not None:
                raise ValueError('Instant carries its own timezone')
            if self.kind=='proxy' and not self.policy:
                raise ValueError('Proxy requires a named policy')
            if self.kind=='exact' and ('proxy' in self.basis or self.policy is not None):
                raise ValueError('A proxy cannot be labeled exact')
        elif self.kind=='date_level':
            value['value']=day(self.value)
            if not self.timezone or self.policy!='end_of_day_v1':
                raise ValueError('Date-level availability requires explicit timezone/end_of_day_v1 policy')
            ZoneInfo(self.timezone)
        elif self.value is not None or self.timezone is not None or self.policy is not None:
            raise ValueError('Unknown/observed-only availability has no invented source time')
        return value


def bound(availability, observed):
    a=Availability(**availability).document()
    if a['kind'] in {'exact','proxy'}:
        return a['value']
    if a['kind']=='date_level':
        next_day=date.fromisoformat(a['value'])+timedelta(days=1)
        return datetime.combine(next_day,time(),ZoneInfo(a['timezone'])).astimezone(timezone.utc).isoformat()
    if a['kind']=='observed_only':
        return instant(observed)
    return None


def eligible(revision, as_of, strictness):
    cutoff=instant(as_of)
    mode=Strictness(strictness)
    doc=revision['document']
    a=doc['availability']
    # ALFRED realtime periods are closed calendar-date intervals. Expired values
    # do not become a fallback when the next vintage is absent or not yet eligible.
    end = doc.get('payload', {}).get('realtime_end') if doc.get('parser_version') == 'alfred-observation-v1' else None
    if end and end != '9999-12-31':
        local_day = datetime.fromisoformat(cutoff).astimezone(ZoneInfo(a['timezone'])).date().isoformat()
        if local_day > end:
            return False
    if mode==Strictness.OBSERVED_ONLY:
        return revision['observed_time']<=cutoff
    if doc.get('data_vintage')=='current_vintage':
        return False
    if a['kind']=='exact':
        return revision['available_bound'] is not None and revision['available_bound']<=cutoff
    if mode==Strictness.ALLOW_PROXY and a['kind'] in {'proxy','date_level'}:
        return revision['available_bound'] is not None and revision['available_bound']<=cutoff
    return False
