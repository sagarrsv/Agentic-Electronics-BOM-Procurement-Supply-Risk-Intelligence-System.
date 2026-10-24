"""Read a BOM with Pandas and return normalized records and validation issues."""
from pathlib import Path
from io import BytesIO

import pandas as pd

from .normalizer import normalize_bom
from .validator import BOMFormatError, validate_bom


def parse_bom(source, filename=None):
    """Accept a CSV/XLSX path, or uploaded bytes/stream with a filename.

    Returns {'items': [...], 'issues': [...], 'summary': {...]}.
    Source values are private and should not be logged or published.
    """
    name = filename or getattr(source, 'name', None) or (str(source) if isinstance(source, (str, Path)) else '')
    extension = Path(name).suffix.lower()
    if extension not in ('.csv', '.xlsx'):
        raise BOMFormatError('Only CSV and XLSX files are supported')

    if isinstance(source, bytes):
        source = BytesIO(source)

    try:
        if extension == '.csv':
            frame = pd.read_csv(source, dtype=str, keep_default_na=False)
        else:
            frame = pd.read_excel(source, dtype=str, keep_default_na=False, engine='openpyxl')
    except (pd.errors.ParserError, pd.errors.EmptyDataError, ValueError, OSError) as exc:
        raise BOMFormatError('Unable to read BOM file') from exc

    if frame.empty:
        raise BOMFormatError('BOM contains no data rows')

    # Drop wholly empty rows (including rows made of whitespace).
    frame = frame.loc[frame.astype(str).apply(lambda col: col.str.strip()).ne('').any(axis=1)]
    if frame.empty:
        raise BOMFormatError('BOM contains no data rows')

    items, issues = validate_bom(normalize_bom(frame.to_dict('records')), list(frame.columns))
    return {
        'items': items,
        'issues': issues,
        'summary': {
            'rows': len(items),
            'valid_rows': sum(i['quantity_per_assembly'] is not None for i in items),
            'errors': sum(i['level'] == 'error' for i in issues),
            'warnings': sum(i['level'] == 'warning' for i in issues),
            'multiple_mpn_rows': sum(len(i['mpn_candidates']) > 1 for i in items),
        },
    }
