
"""Phase 4A: manufacturer aliases + original MPN Mouser lookups.

No MPN correction, no BOM descriptions, no silent substitution.
"""
import re
import time
from collections import defaultdict

from tools.mouser_api import BATCH_SIZE, get_manufacturers, search_v2

GENERIC_MAKES = {"", "unspecified", "unknown", "other", "n/a", "none"}


def make_key(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def mpn_key(value):
    # Keep ordering-suffix punctuation (commas etc.) meaningful.
    return re.sub(r"[-\s]", "", str(value or "").casefold())


def matches(query, returned):
    a, b = mpn_key(query), mpn_key(returned)
    if not a or not b:
        return None
    if str(query).strip().casefold() == str(returned).strip().casefold():
        return "exact"
    if a == b:
        return "format"
    if b.startswith(a):
        return "prefix"
    return None


def orderable(part):
    pn = str(part.get("MouserPartNumber") or "").strip().upper()
    return (pn not in {"", "N/A", "NA"}
            and bool(part.get("PriceBreaks"))
            and part.get("Min") not in (None, ""))


def evidence(mpn, api):
    found = {}
    for part in api.get("parts", []):
        name = str(part.get("ManufacturerPartNumber") or "").strip()
        match = matches(mpn, name)
        if not match:
            continue
        candidate = {
            "mpn": name,
            "manufacturer": part.get("Manufacturer"),
            "mouser_part_number": part.get("MouserPartNumber"),
            "match_type": match,
            "orderable_evidence": orderable(part),
        }
        prior = found.get(name.casefold())
        if prior is None or (candidate["orderable_evidence"] and not prior["orderable_evidence"]):
            found[name.casefold()] = candidate
    priority = {"exact": 0, "format": 1, "prefix": 2}
    return sorted(found.values(), key=lambda x: (
        priority[x["match_type"]], not x["orderable_evidence"]
    ))[:12]


def prepare_bom(items, delay_seconds=2.1, max_single_retries=10,
                model="gemini-3.5-flash-lite", alias_fn=None, official_names=None):
    """Prepare each original manufacturer/MPN option; summarize by BOM item.

    alias_fn(names, official_names) is injectable for tests. The default uses
    one Gemini structured-output call ONLY if there are unmatched manufacturer names.
    """
    records = []
    for item in items:
        for index, option in enumerate(item.get("part_options", [])):
            mpn = str(option.get("mpn") or "").strip()
            if mpn:
                records.append({
                    "key": f"{item['item_id']}:{option.get('role', 'primary')}:{index}",
                    "item_id": item["item_id"],
                    "role": option.get("role", "primary"),
                    "manufacturer": str(option.get("manufacturer") or "").strip(),
                    "mpn": mpn,
                })

    output = {item["item_id"]: [] for item in items}
    metrics = {"manufacturer_list_requests": 0, "gemini_calls": 0,
               "batch_requests": 0, "individual_retries": 0,
               "search_requests": 0, "incomplete_batches": 0, "search_errors": 0}
    if not records:
        return {"items": output, "item_status": {}, "summary": {s: 0 for s in ("resolved", "ambiguous", "unresolved")}, "metrics": metrics}

    if official_names is None:
        official_names = get_manufacturers()
        metrics["manufacturer_list_requests"] = 1
    if not official_names:
        raise ValueError("Mouser manufacturer list is empty")

    official_index = {
        make_key(name): name for name in official_names
        if make_key(name) not in GENERIC_MAKES
    }
    names = list(dict.fromkeys(r["manufacturer"] for r in records))
    unmatched = [n for n in names if make_key(n) not in official_index]

    if unmatched:
        if alias_fn is None:
            from tools.manufacturer_gemini import suggest_manufacturer_aliases
            alias_fn = lambda missing, available: suggest_manufacturer_aliases(
                missing, available, model=model)
        proposed = alias_fn(unmatched, official_names) or {}
        metrics["gemini_calls"] = 1
    else:
        proposed = {}

    mapping = {}
    for name in names:
        exact = official_index.get(make_key(name))
        alias = proposed.get(name) if not exact else None
        validated_alias = official_index.get(make_key(alias)) if alias else None
        mapping[name] = exact or validated_alias

    groups = defaultdict(set)
    plans = {}
    for rec in records:
        official_make = mapping[rec["manufacturer"]]
        pair = (official_make, rec["mpn"]) if official_make else None
        plans[rec["key"]] = (rec, pair)
        if pair:
            groups[official_make].add(rec["mpn"])

    api_results = {}

    def pace():
        if metrics["search_requests"] and delay_seconds:
            time.sleep(delay_seconds)
        metrics["search_requests"] += 1

    for manufacturer, mpn_set in groups.items():
        mpns = sorted(mpn_set)
        for start in range(0, len(mpns), BATCH_SIZE):
            batch = mpns[start:start + BATCH_SIZE]
            metrics["batch_requests"] += 1
            try:
                pace()
                response = search_v2(manufacturer, batch)
                if response["truncated"]:
                    metrics["incomplete_batches"] += 1
                retries = []
                for mpn in batch:
                    hits = evidence(mpn, response)
                    api_results[(manufacturer, mpn)] = {
                        "candidates": hits,
                        "lookup_status": "success" if hits else "no_match",
                        "issues": [],
                        "shared_batch_size": len(batch),
                    }
                    if len(batch) > 1 and (not hits or response["truncated"]):
                        retries.append(mpn)
            except Exception as exc:
                metrics["search_errors"] += 1
                retries = batch
                for mpn in batch:
                    api_results[(manufacturer, mpn)] = {
                        "candidates": [], "lookup_status": "api_error",
                        "issues": [f"Batch search: {type(exc).__name__}: {exc}"],
                    }

            for mpn in retries:
                pair = (manufacturer, mpn)
                if metrics["individual_retries"] >= max_single_retries:
                    api_results[pair]["lookup_status"] = "incomplete"
                    api_results[pair]["issues"].append("Retry budget reached")
                    continue
                metrics["individual_retries"] += 1
                try:
                    pace()
                    single = search_v2(manufacturer, [mpn])
                    hits = evidence(mpn, single)
                    api_results[pair] = {
                        "candidates": hits,
                        "lookup_status": "incomplete" if single["truncated"] else ("success" if hits else "no_match"),
                        "issues": [], "shared_batch_size": 1,
                    }
                except Exception as exc:
                    metrics["search_errors"] += 1
                    api_results[pair] = {
                        "candidates": [], "lookup_status": "api_error",
                        "issues": [f"Single search: {type(exc).__name__}: {exc}"],
                    }

    for rec, pair in plans.values():
        search = api_results.get(pair, {}) if pair else {}
        candidates = search.get("candidates", [])
        lookup = search.get("lookup_status", "manufacturer_unmapped")
        exact = [c for c in candidates if c["match_type"] in ("exact", "format")
                 and c["orderable_evidence"]]
        status = ("ambiguous" if lookup in ("api_error", "incomplete")
                  else "resolved" if exact
                  else "ambiguous" if candidates
                  else "unresolved")
        issues = list(search.get("issues", []))
        if not pair:
            issues.append("Manufacturer not mapped to a validated Mouser name")
        elif lookup == "no_match":
            issues.append("No matching catalog candidate; MPN validity not determined")

        output[rec["item_id"]].append({
            "role": rec["role"],
            "original_manufacturer": rec["manufacturer"],
            "mouser_manufacturer": pair[0] if pair else None,
            "original_mpn": rec["mpn"],
            "status": status,
            "lookup_status": lookup,
            "selected_mpn": exact[0]["mpn"] if status == "resolved" else None,
            "candidates": candidates,
            "candidate_mpns": [c["mpn"] for c in candidates],
            "search_plan": ([{"manufacturer": pair[0], "mpn": pair[1]}] if pair else []),
            "issues": issues,
        })

    item_status = {}
    item_counts = {"resolved": 0, "ambiguous": 0, "unresolved": 0}
    for item_id, entries in output.items():
        statuses = {e["status"] for e in entries}
        status = ("resolved" if "resolved" in statuses else
                  "ambiguous" if "ambiguous" in statuses else "unresolved")
        item_counts[status] += 1
        item_status[item_id] = {
            "resolution_status": status,
            "resolved_options": [e["original_mpn"] for e in entries if e["status"] == "resolved"],
            "ambiguous_options": [e["original_mpn"] for e in entries if e["status"] == "ambiguous"],
            "unresolved_options": [e["original_mpn"] for e in entries if e["status"] == "unresolved"],
        }

    mpn_counts = {s: sum(e["status"] == s for values in output.values() for e in values)
                  for s in ("resolved", "ambiguous", "unresolved")}
    return {
        "items": output,
        "item_status": item_status,
        "summary": item_counts,           # 18 for the existing selected-row test
        "mpn_summary": mpn_counts,         # 27 detailed options
        "manufacturer_mapping": mapping,
        "metrics": metrics,
    }
