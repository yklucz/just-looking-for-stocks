"""Pure provider-envelope contracts. All inputs are retained bytes; no network IO."""
import json
import re
from decimal import Decimal
from .temporal import day, instant

VERSION = 'provider-envelope-v1'


class ContractError(ValueError):
    pass


def decode(body):
    try:
        row = json.loads(body)
        if not isinstance(row, dict):
            raise ValueError()
        return row
    except (ValueError, TypeError):
        raise ContractError('Expected JSON object') from None


def cik_value(value):
    if not re.fullmatch(r'\d{1,10}', str(value)):
        raise ContractError('Invalid SEC CIK')
    return str(value).zfill(10)


def ticker_identity(body, symbol):
    row = decode(body)
    matches = [r for r in row.values() if isinstance(r, dict) and r.get('ticker') == symbol]
    if len(matches) != 1 or not matches[0].get('title'):
        raise ContractError('Ambiguous SEC ticker/entity mapping')
    return {'cik': cik_value(matches[0]['cik_str']), 'name': matches[0]['title'], 'symbol': symbol}


def submissions(body, scope, cik, *, historical=False):
    row = decode(body)
    if not historical and cik_value(row['cik']) != cik:
        raise ContractError('Conflicting SEC entity identity')
    recent = row if historical else row['filings']['recent']
    required = ('accessionNumber', 'form', 'filingDate', 'reportDate', 'acceptanceDateTime', 'primaryDocument')
    if any(not isinstance(recent.get(k), list) for k in required):
        raise ContractError('SEC filing arrays absent')
    size = len(recent['accessionNumber'])
    if any(len(recent[k]) != size for k in required):
        raise ContractError('SEC filing array lengths differ')
    filings = []
    for i in range(size):
        item = {k: recent[k][i] for k in required}
        day(item['filingDate'])
        if not scope['start'] <= item['filingDate'] <= scope['end'] or item['form'] not in scope['forms']:
            continue
        if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', item['accessionNumber']):
            raise ContractError('Invalid SEC accession')
        if item['reportDate']:
            day(item['reportDate'])
        if item['acceptanceDateTime']:
            instant(item['acceptanceDateTime'])
        filings.append(dict(item, cik=cik))
    pages = []
    for page in ([] if historical else row['filings'].get('files', [])):
        if day(page['filingFrom']) <= scope['end'] and day(page['filingTo']) >= scope['start']:
            if not re.fullmatch(r'CIK' + cik + r'-submissions-\d+\.json', page['name']):
                raise ContractError('Invalid SEC historical page identity')
            pages.append(page['name'])
    return {'records': filings, 'pages': sorted(set(pages)), 'count': len(filings)}


def assets(body, scope, cik):
    row = decode(body)
    if cik_value(row['cik']) != cik or row['taxonomy'] != 'us-gaap' or row['tag'] != 'Assets':
        raise ContractError('Unexpected SEC XBRL concept/entity')
    if not isinstance(row.get('units'), dict):
        raise ContractError('XBRL units absent')
    records, unsupported = [], 0
    for unit, facts in row['units'].items():
        if not isinstance(facts, list):
            raise ContractError('XBRL facts must be arrays')
        for item in facts:
            if not scope['start'] <= day(item['filed']) <= scope['end'] or item.get('form') not in scope['forms']:
                continue
            if item.get('dimensions') or item.get('start') or unit != 'USD':
                unsupported += 1
                continue
            if not Decimal(str(item['val'])).is_finite() or isinstance(item['val'], bool):
                raise ContractError('Invalid XBRL value')
            if not re.fullmatch(r'\d{10}-\d{2}-\d{6}', item['accn']):
                raise ContractError('Invalid XBRL accession')
            records.append(dict(cik=cik, taxonomy='us-gaap', concept='Assets', unit=unit,
                                instant=day(item['end']), dimensions={}, accession=item['accn'],
                                value=item['val'], filed=item['filed']))
    return {'records': records, 'unsupported': unsupported, 'count': len(records)}


def alfred(body, scope, series, offset, *, discovery=False):
    row = decode(body)
    key = 'vintage_dates' if discovery else 'observations'
    values = row.get(key)
    if not isinstance(values, list) or any(type(row.get(k)) is not int for k in ('count', 'offset', 'limit')):
        raise ContractError('ALFRED pagination contract changed')
    if row['offset'] != offset or row['limit'] != scope['page_size'] or row['count'] < 0:
        raise ContractError('ALFRED page identity mismatch')
    if len(values) != min(scope['page_size'], max(0, row['count'] - offset)):
        raise ContractError('Incomplete ALFRED page')
    records = []
    for item in values:
        if discovery:
            if not scope['start'] <= day(item) <= scope['end']:
                raise ContractError('Vintage outside requested interval')
            records.append(item)
            continue
        start, end = day(item['realtime_start']), day(item['realtime_end'])
        date = day(item['date'])
        if start > end or start < scope['start'] or end > scope['end']:
            raise ContractError('Realtime interval outside requested period')
        if not scope['observation_start'] <= date <= scope['observation_end']:
            raise ContractError('Observation outside requested window')
        if item['value'] != '.' and not Decimal(str(item['value'])).is_finite():
            raise ContractError('Invalid ALFRED value')
        records.append(dict(series_id=series, observation_date=date, value=item['value'],
                            realtime_start=start, realtime_end=end, vintage_date=start))
    return {'records': records, 'count': row['count'], 'offset': offset,
            'next_offset': offset + len(values), 'complete': offset + len(values) >= row['count']}
