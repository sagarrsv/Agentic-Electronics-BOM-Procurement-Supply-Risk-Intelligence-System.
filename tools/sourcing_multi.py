"""Read-only Mouser + DigiKey sourcing snapshot. No supplier decisions here."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from datetime import datetime, timezone

from tools.sourcing_mouser import source_bom, _mpn_key
from tools.sourcing_mouser_async import fetch_mouser_concurrent
from tools.sourcing_digikey import source_digikey_concurrent, digikey_result_record
from tools.mouser_api import get_manufacturers
from tools.manufacturer_gemini import resolve_from_directories
from tools.digikey_api import DigiKeyClient


def source_bom_two_suppliers(rows, assemblies, verbose=True, digikey_client=None,
                             mouser_lookup=None, delay_seconds=2.1,
                             manufacturer_resolver=None, progress=None, **mouser_kwargs):
    """Fetch all original MPNs from both suppliers; preserve supplier-specific facts.

    This stage is only data collection. Phase 6 decides coverage/risk; Phase 7
    evaluates price breaks and combinations. No substitutions are applied.
    """
    # Resolve manufacturer identities BEFORE any supplier product lookup.
    # Both official directories are supplier data, not Gemini guesses.
    dk = digikey_client or DigiKeyClient()
    passed_official = mouser_kwargs.pop('official_names', None)
    official = list(passed_official if passed_official is not None else
                    ([str(row.get('MAKE') or row.get('manufacturer') or '').strip()
                      for row in rows] if mouser_lookup is not None else get_manufacturers()))
    # Optional injected directory for offline tests; live uses DigiKey V4 endpoint.
    dk_directory = mouser_kwargs.pop('digikey_manufacturers', None)
    dk_directory_was_injected = dk_directory is not None
    if dk_directory is None:
        dk_directory = dk.manufacturers()
    if not official or not dk_directory:
        raise ValueError('Both supplier manufacturer directories must be available')
    bom_names = [str(row.get('MAKE') or row.get('manufacturer') or '').strip() for row in rows]
    resolver = manufacturer_resolver or resolve_from_directories
    if progress:
        progress('manufacturer_verification_started', total=len(set(bom_names)))
    mouser_mapping, digikey_mapping, gemini_calls = resolver(bom_names, official, dk_directory)
    if progress:
        progress('manufacturer_verified', total=len(set(bom_names)),
                 mapped=sum(bool(mouser_mapping.get(x)) for x in set(bom_names)),
                 digikey_verified=sum(bool(digikey_mapping.get(x)) for x in set(bom_names)))
    if verbose:
        print('=== VERIFIED MOUSER MANUFACTURERS ===', mouser_mapping, flush=True)
        print('=== VERIFIED DIGIKEY MANUFACTURERS ===', digikey_mapping, flush=True)

    # Preserve sequential per-supplier calls and Mouser's request pacing.
    # The only concurrency here is DigiKey branch vs Mouser branch.
    def mouser_branch():
        prefetched = None
        if mouser_lookup is None:
            prefetched = fetch_mouser_concurrent(rows, mouser_mapping)
        return source_bom(rows, assemblies, lookup=mouser_lookup,
                          delay_seconds=0 if prefetched is not None else delay_seconds,
                          verbose=verbose, manufacturer_mapping=mouser_mapping,
                          official_names=official, progress=None,
                          prefetched=prefetched, **mouser_kwargs)

    if progress:
        progress('digikey_started', total=len(rows))
        progress('mouser_started', total=len(rows))
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix='supplier') as executor:
        mouser_future = executor.submit(mouser_branch)
        dk_future = executor.submit(source_digikey_concurrent, rows, dk, digikey_mapping)
        # Both submissions happen before waiting for either branch.
        mouser = mouser_future.result()
        dk_cache, dk_errors = dk_future.result()

    mouser['metrics']['gemini_alias_calls'] = gemini_calls
    mouser['metrics']['manufacturer_list_requests'] = int(passed_official is None and mouser_lookup is None)
    if progress:
        progress('mouser_completed', total=len(rows), matched=sum(
            bool(x.get('selected_offer')) for x in mouser['items']))
        progress('digikey_candidates_ready', completed=len(dk_cache), total=len(rows), errors=len(dk_errors))
    items = []
    for i, row in enumerate(mouser['items'], 1):
        mpn, make = row['mpn'], row['manufacturer']
        if verbose:
            print(f'[DigiKey {i}/{len(rows)}] {row["item_id"]}: {make} / {mpn}', flush=True)
        key = (mpn.casefold(), make)
        dk_record = digikey_result_record(mpn, make, dk_cache, dk_errors, digikey_mapping)
        mouser_offer = dict(row['selected_offer']) if row.get('selected_offer') else None
        if mouser_offer:
            mouser_offer['supplier'] = 'Mouser'
        items.append({
            'item_id': row['item_id'], 'manufacturer': make, 'mpn': mpn,
            'quantity_per_assembly': row.get('quantity_per_assembly'),
            'required_quantity': row.get('required_quantity'),
            'supplier_results': [
                {'supplier': 'Mouser', 'lookup_status': row['sourcing_status'],
                 'offers': [mouser_offer] if mouser_offer else [],
                 'related_variants': row.get('related_variants', []),
                 'issues': row.get('issues', [])},
                dk_record,
            ],
        })
    if progress:
        progress("digikey_verified", total=len(items), matched=sum(
            x["supplier_results"][1]["lookup_status"] == "matched" for x in items))
    return {
        'assemblies': assemblies, 'items': items,
        'summary': {
            'mouser': mouser['summary'],
            'digikey': dict(Counter(x['supplier_results'][1]['lookup_status'] for x in items)),
        },
        'metrics': {
            'mouser': mouser['metrics'],
            'digikey_product_requests': getattr(dk, 'requests_made', None),
            'supplier_overlap': {'enabled': True, 'mode': 'parallel_supplier_branches'},
            'digikey_manufacturer_directory_requests': int(not dk_directory_was_injected),
            'verified_manufacturers': {'mouser': sum(bool(x) for x in mouser_mapping.values()),
                                       'digikey': sum(bool(x) for x in digikey_mapping.values())},
        },
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'digikey_locale': {'site': __import__('os').getenv('DIGIKEY_SITE', 'US'),
                           'currency': __import__('os').getenv('DIGIKEY_CURRENCY', 'USD')},
        'note': 'Read-only normalized snapshot: supplier inventories are not yet summed, priced or optimized. No substitutions approved.',
    }
