"""Read-only Mouser sourcing snapshot for clean manufacturer/MPN BOMs.

Descriptions are ignored. Original MPNs are not replaced or corrected.
"""
import re
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from tools.mouser_api import get_manufacturers, search_v2
from langsmith import traceable


def _norm(text: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").casefold())


def _mpn_key(text: Any) -> str:
    # Allow harmless punctuation/spacing variations, not changed suffixes.
    return re.sub(r"[-\s]", "", str(text or "").casefold())


def _decimal(value: Any) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    cleaned = re.sub(r"[^0-9.\-]", "", str(value))
    try:
        result = Decimal(cleaned)
        return result if result >= 0 and result.is_finite() else None
    except InvalidOperation:
        return None


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(str(value).strip().replace(",", ""))
        return parsed if parsed > 0 else None
    except (ValueError, TypeError):
        return None


def _stock(raw: Any) -> dict[str, Any]:
    """Stock is unknown unless a clear numeric stock statement exists."""
    if raw is None:
        return {"quantity": None, "reported_text": None}
    text = str(raw).strip()
    # Common Mouser phrasing: '12,345 In Stock'. Do NOT infer zero from 'on order'.
    found = re.search(r"\b([\d,]+)\s+(?:in\s+stock|available)\b", text, flags=re.I)
    if found:
        return {"quantity": int(found.group(1).replace(",", "")), "reported_text": text}
    if re.fullmatch(r"\s*0\s+(?:in\s+stock|available)\s*", text, flags=re.I):
        return {"quantity": 0, "reported_text": text}
    return {"quantity": None, "reported_text": text}


def _price_breaks(part: dict) -> list[dict]:
    breaks = []
    for tier in part.get("PriceBreaks") or []:
        qty = _positive_int(tier.get("Quantity"))
        price = _decimal(tier.get("Price"))
        if qty is None or price is None:
            continue
        breaks.append({"min_quantity": qty, "unit_price": str(price), "currency": tier.get("Currency") or None})
    return sorted(breaks, key=lambda item: item["min_quantity"])




def _candidate(part: dict, make: str, mpn: str) -> dict | None:
    returned_mpn = str(part.get("ManufacturerPartNumber") or "").strip()
    if not returned_mpn:
        return None
    if returned_mpn.casefold() == mpn.casefold():
        match_type = "exact"
    elif _mpn_key(returned_mpn) == _mpn_key(mpn):
        match_type = "format"
    else:
        return None  # Suffix variants are NOT interchangeable identities.
    reported_make = part.get("Manufacturer")
    stock = _stock(part.get("Availability"))
    mouser_number = part.get("MouserPartNumber")
    return {
        "manufacturer": reported_make,
        "mpn": returned_mpn,
        "match_type": match_type,
        "mouser_part_number": mouser_number,
        "product_url": part.get("ProductDetailUrl") or None,
        "stock_quantity": stock["quantity"],
        "stock_text": stock["reported_text"],
        "lead_time_text": part.get("LeadTime") or None,
        "lead_time_type": "supplier_replenishment_or_manufacturer_lead_time_unconfirmed",
        "lifecycle_text": next((str(part[k]).strip() for k in
                                ("LifecycleStatus", "Lifecycle", "ProductStatus")
                                if part.get(k)), None),
        "lifecycle_evidence_field": next((k for k in
                                           ("LifecycleStatus", "Lifecycle", "ProductStatus")
                                           if part.get(k)), None),
        "minimum_order_quantity": _positive_int(part.get("Min")),
        "order_multiple": _positive_int(part.get("Mult")),
        "price_breaks": _price_breaks(part),
        "orderable_listing": bool(mouser_number and str(mouser_number).strip().upper() not in ("NA", "N/A") and part.get("PriceBreaks")),
    }


def _variants(parts: list[dict], make: str, mpn: str) -> list[dict]:
    """Related catalog identities are leads only; do not treat them as stock offers."""
    found = []
    seen = set()
    key = _mpn_key(mpn)
    for part in parts:
        candidate = str(part.get("ManufacturerPartNumber") or "").strip()
        reported_make = str(part.get("Manufacturer") or "")
        ckey = _mpn_key(candidate)
        if (not key or not ckey or ckey == key or not ckey.startswith(key)
                or candidate.casefold() in seen):
            continue
        seen.add(candidate.casefold())
        found.append({"mpn": candidate, "manufacturer": reported_make,
                      "product_url": part.get("ProductDetailUrl") or None,
                      "note": "Unverified suffix variant; not an approved substitution"})
    return found[:12]


def resolve_manufacturers(names, official_names=None, alias_fn=None, verbose=False):
    """Match names to Mouser's list; ask Gemini once for only unmatched names."""
    official = list(official_names if official_names is not None else get_manufacturers())
    if not official:
        raise ValueError("Mouser returned no manufacturer names")
    index = {_norm(name): name for name in official}
    blocked = {"unspecified", "unknown", "other", "none", "na"}
    mapping = {}
    missing = []
    for name in dict.fromkeys(str(x or "").strip() for x in names):
        direct = index.get(_norm(name))
        if direct and _norm(direct) not in blocked:
            mapping[name] = direct
        else:
            missing.append(name)

    if missing:
        if alias_fn is None:
            from tools.manufacturer_gemini import suggest_manufacturer_aliases
            alias_fn = suggest_manufacturer_aliases
        proposals = alias_fn(missing, official)
        for name in missing:
            proposed = proposals.get(name)
            catalog_name = index.get(_norm(proposed)) if proposed else None
            mapping[name] = catalog_name if catalog_name and _norm(catalog_name) not in blocked else None

    if verbose:
        print("\n=== MANUFACTURER MAPPING ===", flush=True)
        for name, mapped in mapping.items():
            print(f"  {name!r} -> {mapped!r}", flush=True)
    return mapping

@traceable(name="Mouser Sourcing", run_type="chain")
def source_bom(rows, assemblies, lookup=None, delay_seconds=2.1, verbose=False,
               manufacturer_mapping=None, official_names=None, alias_fn=None, progress=None):
    """Check each BOM row against Mouser; never infer approval of suffix variants.

    `lookup` is an optional one-argument mock used by offline tests.
    """
    if not isinstance(assemblies, int) or assemblies <= 0:
        raise ValueError("assemblies must be a positive integer")

    # One manufacturer-list request; Gemini is called only if names don't match.
    live = lookup is None
    names = [str(row.get("MAKE") or row.get("manufacturer") or "").strip() for row in rows]
    unmatched = []
    manufacturer_list_calls = int(live and manufacturer_mapping is None and official_names is None)
    if manufacturer_mapping is None:
        if live:
            official = list(official_names if official_names is not None else get_manufacturers())
            index = {_norm(x): x for x in official}
            blocked = {"unspecified", "unknown", "other", "none", "na"}
            unmatched = [n for n in dict.fromkeys(names)
                         if not index.get(_norm(n)) or _norm(index[_norm(n)]) in blocked]
            manufacturer_mapping = resolve_manufacturers(
                names, official_names=official, alias_fn=alias_fn, verbose=verbose)
        else:
            manufacturer_mapping = {n: n for n in names}

    cache = {}
    results = []
    searches = 0
    for i, row in enumerate(rows, start=1):
        item_id = str(row.get("ID") or row.get("item_id") or "").strip()
        make = str(row.get("MAKE") or row.get("manufacturer") or "").strip()
        mpn = str(row.get("MPN") or row.get("mpn") or "").strip()
        quantity = _positive_int(row.get("Quantity") or row.get("quantity_per_assembly"))
        base = {"item_id": item_id, "manufacturer": make, "mpn": mpn, "supplier": "Mouser"}
        if not all((item_id, make, mpn, quantity)):
            results.append({**base, "sourcing_status": "invalid_input", "selected_offer": None,
                            "issues": ["Missing ID, MAKE, MPN or positive Quantity"]})
            continue

        needed = assemblies * quantity
        base.update(quantity_per_assembly=quantity, required_quantity=needed)
        catalog_make = manufacturer_mapping.get(make)
        if verbose:
            print(f"[{i}/{len(rows)}] {item_id}: {make} / {mpn} | required={needed}", flush=True)
        if not catalog_make:
            results.append({**base, "sourcing_status": "manufacturer_unmapped",
                            "selected_offer": None, "catalog_candidates": [], "related_variants": [],
                            "issues": ["No validated Mouser manufacturer name"]})
            if verbose:
                print("    -> manufacturer_unmapped", flush=True)
            continue

        # Repeated manufacturer/MPN pairs reuse the response within this run.
        key = (catalog_make, mpn)
        try:
            if key not in cache:
                if searches and delay_seconds:
                    time.sleep(delay_seconds)
                searches += 1
                if verbose:
                    print(f"    -> Mouser v2 [{searches}]: {catalog_make} / {mpn}", flush=True)
                cache[key] = search_v2(catalog_make, [mpn]) if live else lookup(mpn)
            elif verbose:
                print("    -> Reusing earlier lookup", flush=True)
            api = cache[key]
            parts = api.get("parts") or []
            if verbose:
                print(f"    -> Returned {len(parts)} products; total={api.get('reported_total')}", flush=True)
                print("    -> Preview:", [f"{p.get('Manufacturer')} / {p.get('ManufacturerPartNumber')}"
                                         for p in parts[:3]], flush=True)

            # Match exact identities only; report suffix variants as unapproved leads.
            candidates = [c for p in parts if (c := _candidate(p, catalog_make, mpn)) is not None]
            variants = _variants(parts, catalog_make, mpn)
            selected = candidates[0] if len(candidates) == 1 else None
            if not candidates:
                status = "catalog_not_matched"
            elif len(candidates) > 1:
                status = "multiple_listings"
            elif not selected["orderable_listing"]:
                status = "ordering_unconfirmed"
            elif selected["stock_quantity"] is None:
                status = "stock_unknown"
            elif selected["stock_quantity"] >= needed:
                status = "stock_covered"
            else:
                status = "stock_shortage"

            results.append({**base, "mouser_manufacturer": catalog_make,
                            "sourcing_status": status, "selected_offer": selected,
                            "catalog_candidates": candidates, "related_variants": variants,
                            "checked_at_utc": datetime.now(timezone.utc).isoformat(),
                            "issues": [] if candidates else ["No exact catalog identity matched; MPN validity unknown"]})
            if verbose:
                print(f"    -> {status} | exact={len(candidates)} | variants={len(variants)} "
                      f"| stock={selected['stock_quantity'] if selected else None}", flush=True)
        except Exception as exc:
            if verbose:
                print(f"    -> API ERROR: {type(exc).__name__}: {exc}", flush=True)
            results.append({**base, "sourcing_status": "api_error", "selected_offer": None,
                            "catalog_candidates": [], "related_variants": [],
                            "issues": [f"{type(exc).__name__}: {exc}"]})

        if progress:
            progress("mouser_progress", completed=i, total=len(rows), status=results[-1]["sourcing_status"])

    counts = {}
    for item in results:
        status = item["sourcing_status"]
        counts[status] = counts.get(status, 0) + 1
    return {"assemblies": assemblies, "items": results, "summary": counts,
            "metrics": {"unique_api_search_attempts": searches, "bom_items": len(rows),
                        "manufacturer_list_requests": manufacturer_list_calls,
                        "gemini_alias_calls": int(live and bool(unmatched))},
            "note": "Supplier snapshot only; reported lead time is not guaranteed delivery. No substitutions approved."}
