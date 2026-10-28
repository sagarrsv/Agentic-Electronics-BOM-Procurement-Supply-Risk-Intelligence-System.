"""Read-only Mouser + DigiKey sourcing snapshot. No supplier decisions here."""
import os
import re
from collections import Counter
from datetime import datetime, timezone

from tools.sourcing import source_bom, _mpn_key, _positive_int, _decimal
from tools.mouser_api import get_manufacturers
from tools.manufacturer_gemini import resolve_supplier_manufacturers, name_key
from tools.digikey_api import DigiKeyClient


def same_manufacturer(bom_name, catalog_name, verified=None):
    """Use exact normalized names or explicit per-run Gemini verification."""
    if not name_key(bom_name) or not name_key(catalog_name):
        return False
    return name_key(bom_name) == name_key(catalog_name) or bool(
        verified and verified.get((bom_name, catalog_name), False))


def _price_breaks(pricing):
    tiers = []
    for entry in pricing or []:
        quantity = _positive_int(entry.get('BreakQuantity') or entry.get('Quantity'))
        price = _decimal(entry.get('UnitPrice') or entry.get('Price'))
        if quantity and price is not None:
            tiers.append({'min_quantity': quantity, 'unit_price': str(price)})
    return sorted(tiers, key=lambda x: x['min_quantity'])


def digikey_offer(product, bom_make, bom_mpn, verified=None):
    """Accept only matching manufacturer AND exact/format-equivalent original MPN.

    DigiKey may return several variations (packaging/ordering SKUs). Preserve
    variation-level offers; never aggregate their stock or treat variants as substitutes.
    """
    if not isinstance(product, dict):
        return None, 'catalog_not_matched'
    returned_mpn = str(product.get('ManufacturerProductNumber') or '').strip()
    manufacturer = product.get('Manufacturer') or {}
    reported_make = manufacturer.get('Name') if isinstance(manufacturer, dict) else str(manufacturer)
    if _mpn_key(returned_mpn) != _mpn_key(bom_mpn):
        return None, 'catalog_not_matched'
    if not same_manufacturer(bom_make, reported_make, verified):
        return None, 'manufacturer_mismatch'

    variations = product.get('ProductVariations') or []
    if not variations:
        variations = [product]
    offers = []
    for var in variations:
        if not isinstance(var, dict):
            continue
        # Availability may be absent; never infer zero from missing data.
        stock = var.get('QuantityAvailable')
        if stock is None:
            stock = product.get('QuantityAvailable')
        if stock is not None:
            try:
                stock = int(stock)
            except (TypeError, ValueError):
                stock = None
        pricing = var.get('StandardPricing') or product.get('StandardPricing') or []
        offers.append({
            'supplier': 'DigiKey',
            'supplier_part_number': var.get('DigiKeyProductNumber') or product.get('DigiKeyPartNumber'),
            'stock_quantity': stock,
            'minimum_order_quantity': _positive_int(var.get('MinimumOrderQuantity') or product.get('MinimumOrderQuantity')),
            # StandardPackage is packaging size, NOT a confirmed ordering increment.
            'order_multiple': _positive_int(var.get('QuantityIncrement') or product.get('QuantityIncrement')),
            'price_breaks': _price_breaks(pricing),
            'currency': (var.get('Currency') or product.get('Currency') or
                         os.getenv('DIGIKEY_CURRENCY', 'USD')),  # requested catalog locale
            'product_url': product.get('ProductUrl'),
            'lead_time_text': product.get('ManufacturerLeadWeeks'),
            'lead_time_type': 'manufacturer_lead_weeks' if product.get('ManufacturerLeadWeeks') is not None else 'unknown',
            'lifecycle_text': (product.get('ProductStatus') or {}).get('Status') if isinstance(product.get('ProductStatus'), dict) else product.get('ProductStatus'),
        })
    return offers, 'matched' if offers else 'ordering_unconfirmed'


def _digikey_candidates(dk, mpn):
    """Collect exact and alternative ordering codes without deciding identity yet."""
    initial = dk.product_details(mpn)
    candidates = [initial] if isinstance(initial, dict) else []
    # Keyword search is needed even if ProductDetails returns a different suffix.
    if hasattr(dk, 'keyword_search'):
        candidates.extend(dk.keyword_search(mpn))
    return candidates


def _find_digikey(dk, mpn, make, candidates=None, verified=None):
    candidates = candidates if candidates is not None else _digikey_candidates(dk, mpn)
    status = 'catalog_not_matched'
    for product in candidates:
        offers, outcome = digikey_offer(product, make, mpn, verified)
        if outcome == 'matched':
            sku = product.get('DigiKeyPartNumber') or product.get('DigiKeyProductNumber')
            if sku and hasattr(dk, 'product_details'):
                full = dk.product_details(sku)
                detailed, detailed_status = digikey_offer(full, make, mpn, verified)
                if detailed_status == 'matched':
                    return detailed, detailed_status, candidates
            return offers, outcome, candidates
        if outcome == 'manufacturer_mismatch':
            status = 'manufacturer_mismatch'
    return None, status, candidates


def source_bom_two_suppliers(rows, assemblies, verbose=True, digikey_client=None,
                             mouser_lookup=None, delay_seconds=2.1,
                             manufacturer_resolver=None, **mouser_kwargs):
    """Fetch all original MPNs from both suppliers; preserve supplier-specific facts.

    This stage is only data collection. Phase 6 decides coverage/risk; Phase 7
    evaluates price breaks and combinations. No substitutions are applied.
    """
    # DigiKey first: its actual returned labels can be verified in the SAME
    # Gemini call as the Mouser BOM->official manufacturer mapping.
    dk = digikey_client or DigiKeyClient()
    dk_cache = {}
    dk_errors = {}
    for row in rows:
        mpn = str(row.get('MPN') or row.get('mpn') or '').strip()
        key = mpn.casefold()
        if key and key not in dk_cache and key not in dk_errors:
            try:
                dk_cache[key] = _digikey_candidates(dk, mpn)
            except Exception as exc:
                dk_errors[key] = f'{type(exc).__name__}: {exc}'

    def manufacturer_of(product):
        reported = product.get('Manufacturer') or {}
        return reported.get('Name') if isinstance(reported, dict) else str(reported)

    # Only include DigiKey labels on exact or prefix-related MPN hits.
    # A broad keyword search (e.g., S1M) can contain many unrelated brands.
    dk_options = {}
    for row in rows:
        make = str(row.get('MAKE') or row.get('manufacturer') or '').strip()
        mpn = str(row.get('MPN') or row.get('mpn') or '').strip()
        key = _mpn_key(mpn)
        for product in dk_cache.get(mpn.casefold(), []):
            if not isinstance(product, dict):
                continue
            returned = _mpn_key(product.get('ManufacturerProductNumber'))
            label = manufacturer_of(product)
            if key and returned.startswith(key) and label:
                dk_options.setdefault(make, [])
                if label not in dk_options[make]:
                    dk_options[make].append(label)
    passed_official = mouser_kwargs.pop('official_names', None)
    official = list(passed_official if passed_official is not None else
                    ([str(row.get('MAKE') or row.get('manufacturer') or '').strip()
                      for row in rows] if mouser_lookup is not None else get_manufacturers()))
    resolver = manufacturer_resolver or resolve_supplier_manufacturers
    aliases, verified, gemini_calls = resolver(
        [str(row.get('MAKE') or row.get('manufacturer') or '').strip() for row in rows],
        official, dk_options)
    if verbose:
        print('\n=== MANUFACTURER MAPPING (Mouser) ===', flush=True)
        for name, mapped in aliases.items():
            print(f'  {name!r} -> {mapped!r}', flush=True)
        print('=== DIGIKEY MANUFACTURER MAPPING ===', flush=True)
        for make, labels in dk_options.items():
            matches = [label for label in labels if verified.get((make, label))]
            print(f'  {make!r} -> {matches[0] if matches else None!r}', flush=True)
    mouser = source_bom(rows, assemblies, lookup=mouser_lookup,
                        delay_seconds=delay_seconds, verbose=verbose,
                        manufacturer_mapping=aliases, official_names=official,
                        **mouser_kwargs)
    # source_bom skips its built-in Gemini when manufacturer_mapping is supplied.
    mouser['metrics']['gemini_alias_calls'] = gemini_calls
    mouser['metrics']['manufacturer_list_requests'] = int(passed_official is None and mouser_lookup is None)
    items = []
    for i, row in enumerate(mouser['items'], 1):
        mpn, make = row['mpn'], row['manufacturer']
        if verbose:
            print(f'[DigiKey {i}/{len(rows)}] {row["item_id"]}: {make} / {mpn}', flush=True)
        key = mpn.casefold()
        try:
            if key in dk_errors:
                raise RuntimeError(dk_errors[key])
            offers, status, candidates = _find_digikey(
                dk, mpn, make, dk_cache.get(key, []), verified)
            related = []
            for product in candidates:
                if not isinstance(product, dict):
                    continue
                candidate_mpn = str(product.get('ManufacturerProductNumber') or '').strip()
                reported = product.get('Manufacturer') or {}
                candidate_make = reported.get('Name') if isinstance(reported, dict) else str(reported)
                if (candidate_mpn and _mpn_key(candidate_mpn) != _mpn_key(mpn)
                        and _mpn_key(candidate_mpn).startswith(_mpn_key(mpn))
                        and same_manufacturer(make, candidate_make, verified)):
                    related.append({'mpn': candidate_mpn, 'manufacturer': candidate_make,
                                    'note': 'Unverified related suffix; not an approved substitute'})
            dk_record = {'supplier': 'DigiKey', 'lookup_status': status, 'offers': offers or [],
                         'related_variants': list({x['mpn']: x for x in related}.values()),
                         'candidate_preview': [(p.get('ManufacturerProductNumber'),
                             (p.get('Manufacturer') or {}).get('Name'))
                             for p in candidates if isinstance(p, dict)][:10], 'error': None}
            if verbose:
                print(f'    -> {status}; variations={len(offers or [])}; stock={[o["stock_quantity"] for o in offers or []]}', flush=True)
                if status != 'matched':
                    print(f'    -> candidates: {dk_record["candidate_preview"]}', flush=True)
        except Exception as exc:
            dk_record = {'supplier': 'DigiKey', 'lookup_status': 'api_error', 'offers': [],
                         'error': f'{type(exc).__name__}: {exc}'}
            if verbose:
                print(f'    -> API ERROR: {dk_record["error"]}', flush=True)
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
    return {
        'assemblies': assemblies, 'items': items,
        'summary': {
            'mouser': mouser['summary'],
            'digikey': dict(Counter(x['supplier_results'][1]['lookup_status'] for x in items)),
        },
        'metrics': {
            'mouser': mouser['metrics'],
            'digikey_product_requests': getattr(dk, 'requests_made', None),
        },
        'checked_at_utc': datetime.now(timezone.utc).isoformat(),
        'digikey_locale': {'site': __import__('os').getenv('DIGIKEY_SITE', 'US'),
                           'currency': __import__('os').getenv('DIGIKEY_CURRENCY', 'USD')},
        'note': 'Read-only normalized snapshot: supplier inventories are not yet summed, priced or optimized. No substitutions approved.',
    }
