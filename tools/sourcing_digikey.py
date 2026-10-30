"""DigiKey-only product sourcing, exact MPN/manufacturer validation, and fallback."""
import os
from tools.sourcing_mouser import _mpn_key, _positive_int, _decimal
from tools.manufacturer_gemini import name_key
from langsmith import traceable

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


def _digikey_candidates(dk, mpn, manufacturer_id=None):
    """Retrieve product details and keyword candidates with optional verified ID."""
    try:
        initial = dk.product_details(mpn, manufacturer_id=manufacturer_id)
    except TypeError:  # Compatibility with preexisting injected test clients
        initial = dk.product_details(mpn)
    candidates = [initial] if isinstance(initial, dict) else []
    if hasattr(dk, 'keyword_search'):
        try:
            candidates.extend(dk.keyword_search(mpn, manufacturer_id=manufacturer_id))
        except TypeError:
            candidates.extend(dk.keyword_search(mpn))
    return candidates


def _find_digikey(dk, mpn, make, candidates=None, verified=None):
    status = 'catalog_not_matched'
    for product in candidates or []:
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
    return None, status, candidates or []


def source_digikey(rows, dk, digikey_mapping):
    """Sequential per-MPN DigiKey lookups; retain original strict matching."""
    cache, failures = {}, {}
    for row in rows:
        mpn = str(row.get('MPN') or row.get('mpn') or '').strip()
        make = str(row.get('MAKE') or row.get('manufacturer') or '').strip()
        key = (mpn.casefold(), make)
        if not mpn or key in cache or key in failures:
            continue
        identity = digikey_mapping.get(make)
        try:
            if not identity:
                # No verified manufacturer: no unverified product accepted.
                cache[key] = {'offers': None, 'status': 'manufacturer_unmapped',
                              'candidates': [], 'error': None}
                continue
            verified = {(make, identity['name']): True}
            candidates = _digikey_candidates(dk, mpn, identity['id'])
            offers, status, used = _find_digikey(dk, mpn, make, candidates, verified)
            # Filtered search can miss valid listings. Fall back to the
            # original MPN-only search only if no verified exact match.
            if status != 'matched':
                fallback = _digikey_candidates(dk, mpn)
                offers, status, used = _find_digikey(dk, mpn, make, fallback, verified)
                used = candidates + fallback
            cache[key] = {'offers': offers, 'status': status,
                          'candidates': used, 'error': None}
        except Exception as exc:
            failures[key] = f'{type(exc).__name__}: {exc}'
    return cache, failures


@traceable(name="DigiKey Async Candidates", run_type="chain")
async def _async_digikey_candidates(dk, client, mpn, manufacturer_id=None):
    initial = await dk.async_product_details(client, mpn, manufacturer_id=manufacturer_id)
    candidates = [initial] if isinstance(initial, dict) else []
    candidates.extend(await dk.async_keyword_search(client, mpn, manufacturer_id=manufacturer_id))
    return candidates


async def _async_find_digikey(dk, client, mpn, make, candidates, verified):
    status = 'catalog_not_matched'
    for product in candidates:
        offers, outcome = digikey_offer(product, make, mpn, verified)
        if outcome == 'matched':
            sku = product.get('DigiKeyPartNumber') or product.get('DigiKeyProductNumber')
            if sku:
                full = await dk.async_product_details(client, sku)
                detailed, detail_status = digikey_offer(full, make, mpn, verified)
                if detail_status == 'matched':
                    return detailed, detail_status, candidates
            return offers, outcome, candidates
        if outcome == 'manufacturer_mismatch':
            status = 'manufacturer_mismatch'
    return None, status, candidates

@traceable(name="DigiKey Async Sourcing", run_type="chain")
async def source_digikey_async(rows, dk, digikey_mapping, max_concurrency=3):
    """Concurrent independent MPN lookups; preserves validation and fallback.

    Each MPN is processed as one task. Requests within that task retain their
    previous sequence. Distinct MPN tasks share an AsyncClient and a semaphore.
    Existing injected synchronous test clients are still supported.
    """
    import asyncio
    import httpx
    semaphore = asyncio.Semaphore(max_concurrency)
    # The DigiKey client is used from this one event loop in one supplier branch.
    if hasattr(dk, 'async_product_details'):
        dk._async_token_lock = asyncio.Lock()
        dk._async_request_lock = asyncio.Lock()
        dk._async_next_request_at = 0.0
    cache, failures = {}, {}
    unique = {}
    for row in rows:
        mpn = str(row.get('MPN') or row.get('mpn') or '').strip()
        make = str(row.get('MAKE') or row.get('manufacturer') or '').strip()
        key = (mpn.casefold(), make)
        if mpn and key not in unique:
            unique[key] = (mpn, make)

    async with httpx.AsyncClient() as client:
        async def worker(key, mpn, make):
            identity = digikey_mapping.get(make)
            if not identity:
                return key, {'offers': None, 'status': 'manufacturer_unmapped',
                             'candidates': [], 'error': None}, None
            async with semaphore:
                try:
                    verified = {(make, identity['name']): True}
                    if hasattr(dk, 'async_product_details') and hasattr(dk, 'async_keyword_search'):
                        candidates = await _async_digikey_candidates(dk, client, mpn, identity['id'])
                        offers, status, used = await _async_find_digikey(dk, client, mpn, make, candidates, verified)
                        if status != 'matched':
                            fallback = await _async_digikey_candidates(dk, client, mpn)
                            offers, status, used = await _async_find_digikey(dk, client, mpn, make, fallback, verified)
                            used = candidates + fallback
                    else:
                        # Compatibility for tests or custom synchronous DigiKey clients.
                        single_row = {'MPN': mpn, 'MAKE': make}
                        old_cache, old_errors = await asyncio.to_thread(source_digikey, [single_row], dk, digikey_mapping)
                        return key, old_cache.get(key), old_errors.get(key)
                    return key, {'offers': offers, 'status': status,
                                 'candidates': used, 'error': None}, None
                except Exception as exc:
                    return key, None, f'{type(exc).__name__}: {exc}'

        # gather preserves submission order, even if HTTP responses arrive out of order.
        results = await asyncio.gather(*(worker(key, mpn, make)
                                         for key, (mpn, make) in unique.items()))
    for key, result, error in results:
        if error:
            failures[key] = error
        elif result is not None:
            cache[key] = result
    return cache, failures


def source_digikey_concurrent(rows, dk, digikey_mapping, max_concurrency=3):
    """Synchronous boundary for existing ToolNode/Streamlit supplier worker."""
    import asyncio
    return asyncio.run(source_digikey_async(rows, dk, digikey_mapping,
                                            max_concurrency=max_concurrency))

def digikey_result_record(mpn, make, dk_cache, dk_errors, digikey_mapping):
    """Convert supplier retrieval result to the existing normalized offer format."""
    key = (mpn.casefold(), make)
    if key in dk_errors:
        dk_record = {'supplier': 'DigiKey', 'lookup_status': 'api_error', 'offers': [],
                     'error': dk_errors[key]}
    else:
        entry = dk_cache.get(key, {'offers': None, 'status': 'catalog_not_matched',
                                   'candidates': [], 'error': None})
        offers, status, candidates = entry['offers'], entry['status'], entry['candidates']
        identity = digikey_mapping.get(make)
        verified = {(make, identity['name']): True} if identity else {}
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
    return dk_record
