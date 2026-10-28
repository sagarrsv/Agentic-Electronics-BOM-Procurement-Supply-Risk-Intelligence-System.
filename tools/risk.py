"""Phase 6: deterministic risk interpretation of a Phase 5 two-supplier snapshot.

No network calls or LLM judgments. Inputs and original supplier results remain intact.
Scores are policy indicators, not empirical failure probabilities.
"""
from collections import Counter
import re

SUPPLIERS = ('Mouser', 'DigiKey')


def _int(value):
    try:
        n = int(value)
        return n if n >= 0 else None
    except (ValueError, TypeError):
        return None


def _lifecycle(text):
    if not text:
        return 'unknown'
    value = re.sub(r'[^a-z0-9]+', ' ', str(text).lower()).strip()
    if any(s in value for s in ('obsolete', 'end of life', 'discontinued', 'eol')):
        return 'obsolete_eol'
    if any(s in value for s in ('not recommended', 'not for new designs', 'nrnd')):
        return 'nrnd'
    if 'active' in value:
        return 'active'
    return 'unknown'


def _lead_days(offer):
    raw = offer.get('lead_time_text')
    if raw is None:
        return None
    kind = offer.get('lead_time_type')
    if kind == 'manufacturer_lead_weeks':
        try:
            weeks = float(raw)
            return round(weeks * 7) if weeks > 0 else None
        except (TypeError, ValueError):
            return None
    if kind == 'supplier_replenishment_or_manufacturer_lead_time_unconfirmed':
        match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*days?\s*', str(raw), re.I)
        return round(float(match.group(1))) if match and float(match.group(1)) > 0 else None
    return None


def _rating(score):
    return 'critical' if score >= 90 else 'high' if score >= 60 else 'medium' if score >= 30 else 'low'


def evaluate_item(item):
    required = _int(item.get('required_quantity'))
    if required is None or required == 0:
        raise ValueError(f'Item {item.get("item_id")}: required_quantity must be positive')
    supplier_results = {x.get('supplier'): x for x in item.get('supplier_results', [])}
    suppliers = []
    for name in SUPPLIERS:
        result = supplier_results.get(name, {})
        offers = result.get('offers') or []
        match = result.get('lookup_status') in ('matched', 'stock_covered', 'stock_shortage', 'stock_unknown', 'ordering_unconfirmed')
        # Do not accidentally give unrelated/non-exact candidates inventory credit.
        if name == 'Mouser':
            offers = [o for o in offers if o.get('match_type') == 'exact'] if match else []
        elif not match:
            offers = []
        known = [v for v in (_int(o.get('stock_quantity')) for o in offers) if v is not None]
        # DigiKey packaging variations can expose a single shared stock pool.
        # MAX is a safe reported floor, not a guarantee that every offer can draw from it.
        stock = max(known) if known else None
        lifecycle_values = [_lifecycle(o.get('lifecycle_text')) for o in offers]
        if 'obsolete_eol' in lifecycle_values:
            life = 'obsolete_eol'
        elif 'nrnd' in lifecycle_values:
            life = 'nrnd'
        elif 'active' in lifecycle_values:
            life = 'active'
        else:
            life = 'unknown'
        # Supplier's best documented MOQ; keep variations distinct for future optimization.
        moqs = [_int(o.get('minimum_order_quantity')) for o in offers]
        moqs = [m for m in moqs if m and m > 0]
        lead = [_lead_days(o) for o in offers]
        lead = [d for d in lead if d is not None]
        suppliers.append({
            'supplier': name,
            'lookup_status': result.get('lookup_status', 'not_checked'),
            'exact_part_matched': bool(offers),
            'reported_stock': stock,
            'stock_is_unknown': bool(offers) and stock is None,
            'can_cover_alone': bool(stock is not None and stock >= required),
            'lifecycle': life,
            'minimum_documented_moq': min(moqs) if moqs else None,
            'order_multiple_unconfirmed': bool(offers) and any(o.get('order_multiple') is None for o in offers),
            'lead_time_days': min(lead) if lead else None,
            'lead_time_interpretation': ('manufacturer_replenishment' if name == 'DigiKey'
                                         else 'unconfirmed_replenishment'),
            'offer_count': len(offers),
        })
    matched = [s for s in suppliers if s['exact_part_matched']]
    observed = [s for s in suppliers if s['reported_stock'] is not None]
    known_stock = sum(s['reported_stock'] for s in observed)
    unknown = [s['supplier'] for s in matched if s['stock_is_unknown']]
    confirmed_shortfall = max(0, required - known_stock)
    known_complete = bool(matched) and not unknown
    if known_stock >= required:
        availability = 'covered_by_reported_stock'
        availability_score = 0
    elif unknown:
        availability = 'coverage_unconfirmed'
        availability_score = 60
    elif known_stock > 0:
        availability = 'confirmed_reported_shortage'
        availability_score = 90
    else:
        availability = 'no_confirmed_stock'
        availability_score = 90
    if not matched:
        availability = 'no_exact_catalog_match'
        availability_score = 100
    lifecycles = [s['lifecycle'] for s in matched]
    if 'obsolete_eol' in lifecycles:
        life = 'obsolete_eol'
        lifecycle_score = 100
    elif 'nrnd' in lifecycles:
        life = 'nrnd'
        lifecycle_score = 70
    elif lifecycles and all(v == 'active' for v in lifecycles):
        life = 'active'
        lifecycle_score = 0
    else:
        life = 'unknown_or_mixed'
        lifecycle_score = 25  # conservative missing-data flag, not proof of risk
    # Replenishment exposure is only scored when enough stock is not confirmed.
    lead_values = [s['lead_time_days'] for s in matched if s['lead_time_days'] is not None]
    if known_stock >= required:
        lead_score, lead_state = 0, 'not_needed_for_in_stock_quantity'
    elif not lead_values:
        lead_score, lead_state = 35, 'unknown_when_needed'
    else:
        fastest = min(lead_values)
        lead_score = 80 if fastest > 180 else 55 if fastest > 90 else 25
        lead_state = 'replenishment_exposure_not_delivery_eta'
    independent_stocked = [s for s in suppliers if (s['reported_stock'] or 0) > 0]
    concentration_score = 0 if len(independent_stocked) == 2 else 50 if len(independent_stocked) == 1 else 65
    concentration = ('two_suppliers_with_reported_stock' if len(independent_stocked) == 2 else
                     'one_supplier_with_reported_stock' if independent_stocked else 'no_confirmed_stock_supplier')
    # An exact offer with a MOQ over demand is a procurement constraint, not shortage.
    moq_excess = [s['supplier'] for s in matched if s['minimum_documented_moq'] is not None
                  and s['minimum_documented_moq'] > required]
    purchasable = [s for s in matched if s['minimum_documented_moq'] is not None and s['minimum_documented_moq'] <= required]
    moq_score = 0 if purchasable else 45 if moq_excess else 25
    moq_status = 'at_least_one_offer_moq_fits_demand' if purchasable else 'all_known_moq_exceed_demand' if moq_excess else 'moq_unconfirmed'
    unknown_fields = []
    if unknown:
        unknown_fields.append('stock:' + ','.join(unknown))
    if any(s['lifecycle'] == 'unknown' for s in matched):
        unknown_fields.append('lifecycle')
    if any(s['order_multiple_unconfirmed'] for s in matched):
        unknown_fields.append('order_multiple')
    if matched and not lead_values:
        unknown_fields.append('lead_time')
    if any(s['lookup_status'] == 'api_error' for s in suppliers):
        unknown_fields.append('supplier_api_error')
    if not matched:
        unknown_fields.append('no_exact_part_match')
    uncertainty_score = min(70, 15 * len(unknown_fields))
    scores = {'availability': availability_score, 'lifecycle': lifecycle_score,
              'lead_time': lead_score, 'moq': moq_score,
              'supplier_concentration': concentration_score, 'uncertainty': uncertainty_score}
    # Explicit precedence: most severe dimension wins; do not dilute EOL with stocked inventory.
    overall = max(scores.values())
    return {'item_id': item['item_id'], 'manufacturer': item['manufacturer'], 'mpn': item['mpn'],
            'required_quantity': required, 'supplier_assessments': suppliers,
            'inventory': {'known_reported_stock_total': known_stock,
                          'known_stock_shortfall': confirmed_shortfall,
                          'unknown_stock_suppliers': unknown,
                          'coverage_confirmed': known_stock >= required,
                          'all_matched_supplier_stock_known': known_complete,
                          'availability_status': availability},
            'lifecycle': life, 'lead_time_status': lead_state,
            'moq_status': moq_status, 'moq_exceeds_required_suppliers': moq_excess,
            'supplier_concentration': concentration,
            'unknown_fields': unknown_fields, 'risk_scores': scores,
            'overall_risk_score': overall, 'overall_risk_level': _rating(overall)}


def evaluate_bom_coverage(phase5, evaluated_items):
    """BOM-level reported-stock assembly capacity and provisional shortage lead times.

    This is an evidence-based planning view, not a confirmed order promise.
    """
    target = _int(phase5.get('assemblies'))
    if not target:
        raise ValueError('assemblies must be a positive integer')
    if not evaluated_items:
        raise ValueError('BOM must contain at least one item')
    source_by_id = {str(i['item_id']): i for i in phase5['items']}
    rows = []
    for assessed in evaluated_items:
        original = source_by_id[str(assessed['item_id'])]
        per_assembly = _int(original.get('quantity_per_assembly'))
        required = assessed['required_quantity']
        if per_assembly is None and required % target == 0:
            per_assembly = required // target  # backward-compatible derivation for older snapshots
        if not per_assembly or per_assembly * target != required:
            raise ValueError(f"Item {assessed['item_id']}: quantity/assembly * assemblies != required_quantity")
        inv = assessed['inventory']
        known = inv['known_reported_stock_total']
        missing = inv['unknown_stock_suppliers']
        shortage = max(0, required - known)
        # Unknown supplier stock is not a confirmed shortage, nor a confirmed zero.
        shortage_class = ('none' if shortage == 0 else
                          'unconfirmed_due_to_unknown_stock' if missing else
                          'confirmed_in_reported_inventory')
        lead_options = []
        by_supplier = {x['supplier']: x for x in assessed['supplier_assessments']}
        for src in original.get('supplier_results', []):
            name = src.get('supplier')
            if name not in by_supplier:
                continue
            sa = by_supplier[name]
            if not sa['exact_part_matched'] or sa['lifecycle'] == 'obsolete_eol':
                continue
            # Explicitly unorderable listings cannot promise replenishment.
            offers = src.get('offers') or []
            if name == 'Mouser':
                offers = [o for o in offers if o.get('match_type') == 'exact']
            candidate_days = [_lead_days(o) for o in offers
                              if o.get('orderable_listing') is not False]
            candidate_days = [v for v in candidate_days if v is not None]
            if candidate_days:
                lead_options.append({
                    'supplier': name,
                    'lead_time_days': min(candidate_days),
                    'lead_time_type': sa['lead_time_interpretation'],
                    'not_confirmed_delivery_eta': True,
                })
        lead_options.sort(key=lambda x: (x['lead_time_days'], x['supplier']))
        fastest = lead_options[0] if lead_options else None
        rows.append({
            'item_id': assessed['item_id'],
            'manufacturer': assessed['manufacturer'],
            'mpn': assessed['mpn'],
            'quantity_per_assembly': per_assembly,
            'required_quantity': required,
            'mouser_reported_stock': by_supplier['Mouser']['reported_stock'],
            'digikey_reported_stock': by_supplier['DigiKey']['reported_stock'],
            'combined_known_reported_stock': known,
            'units_needed_beyond_known_stock': shortage,
            'shortage_status': shortage_class,
            'assemblies_supported_by_known_stock': min(target, known // per_assembly),
            'stock_unknown_suppliers': missing,
            'full_quantity_covered_by_reported_stock': known >= required,
            'replenishment_options': lead_options if shortage else [],
            'fastest_documented_replenishment_option': fastest if shortage else None,
            'replenishment_status': ('not_needed' if not shortage else
                                     'provisional_lead_time_available' if fastest else
                                     'no_usable_lead_time'),
            'overall_risk_level': assessed['overall_risk_level'],
        })
    minimum_supported = min(x['assemblies_supported_by_known_stock'] for x in rows)
    limiting = [x['item_id'] for x in rows
                if x['assemblies_supported_by_known_stock'] == minimum_supported]
    unresolved = [x['item_id'] for x in rows
                  if x['units_needed_beyond_known_stock'] > 0 and
                  x['fastest_documented_replenishment_option'] is None]
    candidates = [r for r in rows if r['units_needed_beyond_known_stock'] > 0
                  and r['fastest_documented_replenishment_option'] is not None]
    longest = max((r['fastest_documented_replenishment_option']['lead_time_days']
                   for r in candidates), default=None)
    bottleneck = [r['item_id'] for r in candidates if
                  r['fastest_documented_replenishment_option']['lead_time_days'] == longest]
    return {
        'target_assemblies': target,
        'assemblies_supported_by_reported_stock': minimum_supported,
        'full_target_coverage_confirmed': all(r['full_quantity_covered_by_reported_stock'] for r in rows),
        'limiting_items_by_current_reported_stock': limiting,
        'items_with_known_stock_gap': [r['item_id'] for r in rows if r['units_needed_beyond_known_stock'] > 0],
        'items_with_unconfirmed_shortage': [r['item_id'] for r in rows
                                           if r['shortage_status'] == 'unconfirmed_due_to_unknown_stock'],
        'replenishment': {
            'longest_fastest_documented_option_days': longest,
            'provisional_bottleneck_item_ids': bottleneck,
            'unresolved_without_usable_lead_time_item_ids': unresolved,
            'is_confirmed_production_eta': False,
            'explanation': ('Maximum of the shortest documented, potentially usable replenishment '
                            'lead time per part with a known stock gap. Not a supplier delivery promise. '
                            'Unknown stock, ordering conditions, obsolete parts, and unlisted parts '
                            'can invalidate this estimate.'),
        },
        'component_rows': rows,
        'data_quality_notes': [
            'DigiKey packaging variations share reported stock; counted once per supplier.',
            'Unknown supplier inventory is not zero. Assemblies supported are based on known stock only.',
            'Cross-supplier quantities assume independent orderable inventory pools.',
            'Supplier lead times are replenishment indications, not shipment or delivery dates.',
            'No automatic substitutions or confirmed replenishment orders.',
        ],
    }


def evaluate_report(phase5):
    results = [evaluate_item(item) for item in phase5['items']]
    return {'phase': 6, 'method': 'deterministic_v1_max_dimension',
            'checked_at_utc': phase5.get('checked_at_utc'),
            'assemblies': phase5['assemblies'],
            'bom_coverage': evaluate_bom_coverage(phase5, results),
            'summary': {'risk_levels': dict(Counter(r['overall_risk_level'] for r in results)),
                        'total_items': len(results)},
            'policy': {'overall': 'max of six dimension scores; thresholds low<30 medium<60 high<90 critical>=90',
                       'stock': 'max per supplier across packaging offers, then add across suppliers; missing stays unknown',
                       'lead_time': 'replenishment exposure only when reported stock does not cover; never shipping ETA',
                       'lifecycle': 'any EOL/obsolete critical, any NRND high; unknown not active',
                       'substitutions': 'never included'},
            'items': results}
