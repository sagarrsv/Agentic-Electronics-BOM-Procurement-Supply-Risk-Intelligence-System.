
"""Small Mouser client. Single searches are authoritative diagnostic path;
optional pipe-batches are an empirical optimization and must be verified.
"""
import os
import requests
from dotenv import load_dotenv
load_dotenv()
BASE = 'https://api.mouser.com/api/v2/search'
BATCH_SIZE = 1
RESULT_CAP = 50

class MouserAPIError(RuntimeError):
    pass


def _key():
    value = os.getenv('MOUSER_API_KEY')
    if not value:
        raise MouserAPIError('MOUSER_API_KEY missing')
    return value


def _response(r, label):
    try:
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        raise MouserAPIError(f'{label}: {type(e).__name__}: {e}') from e
    errors = data.get('Errors') or []
    if errors:
        message = '; '.join(f"{x.get('Code')}: {x.get('Message')}" for x in errors)
        if all(str(x.get('Code')).lower() == 'notfound' for x in errors):
            return {'SearchResults': {'Parts': [], 'NumberOfResult': 0}, '_not_found': message}
        raise MouserAPIError(f'{label}: {message}')
    return data


def get_manufacturers():
    try:
        r = requests.get(BASE + '/manufacturerlist', params={'apiKey': _key()}, timeout=20)
    except requests.RequestException as e:
        raise MouserAPIError(str(e)) from e
    data = _response(r, 'manufacturerlist')
    found = []
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k.lower() in ('manufacturername', 'name') and isinstance(v, str):
                    found.append(v)
                elif isinstance(v, (list, dict)):
                    walk(v)
        elif isinstance(x, list):
            for v in x: walk(v)
    walk(data)
    if not found:
        for k, v in data.items():
            if 'manufacturer' in k.lower() and isinstance(v, list):
                found.extend(x for x in v if isinstance(x, str))
    return list(dict.fromkeys(found))


def _parse(data):
    result = data.get('SearchResults') or {}
    parts = result.get('Parts') or []
    total = result.get('NumberOfResult')
    try: total = int(total) if total is not None else None
    except (TypeError, ValueError): total = None
    return {'parts': parts, 'reported_total': total, 'returned_count': len(parts),
            'truncated': len(parts) >= RESULT_CAP or (total is not None and total > len(parts)),
            'not_found': data.get('_not_found')}


def search_v2(manufacturer, mpns):
    if not manufacturer or not 1 <= len(mpns) <= BATCH_SIZE:
        raise ValueError('manufacturer required; 1..5 MPNs')
    payload = {'SearchByPartMfrNameRequest': {
        'manufacturerName': manufacturer, 'mouserPartNumber': '|'.join(mpns),
        'partSearchOptions': 'None'}}
    try:
        r = requests.post(BASE + '/partnumberandmanufacturer', params={'apiKey': _key()},
                          json=payload, timeout=20)
    except requests.RequestException as e:
        raise MouserAPIError(str(e)) from e
    return _parse(_response(r, f'v2 {manufacturer}/{mpns}'))


def search_v1(mpn):
    """Single manufacturer-independent fallback (never accept wrong-make candidate blindly)."""
    payload = {'SearchByPartRequest': {'mouserPartNumber': mpn, 'partSearchOptions': 'None'}}
    try:
        r = requests.post('https://api.mouser.com/api/v1/search/partnumber',
                          params={'apiKey': _key()}, json=payload, timeout=20)
    except requests.RequestException as e:
        raise MouserAPIError(str(e)) from e
    return _parse(_response(r, f'v1 {mpn}'))

# Backward-compatible old name; new resolver calls search_v2 directly.
search_mouser_v2 = search_v2
