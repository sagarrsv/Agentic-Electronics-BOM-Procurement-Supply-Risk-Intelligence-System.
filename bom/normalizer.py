"""Pure BOM normalization; never infer electrical interchangeability."""
import re

ALIASES = {
    "id": "source_id", "item id": "source_id", "line": "source_id", "line number": "source_id",
    "description": "description", "component description": "description", "part description": "description",
    "make": "manufacturer", "manufacturer": "manufacturer", "mfr": "manufacturer", "mfg": "manufacturer",
    "part no": "mpn", "part number": "mpn", "manufacturer part number": "mpn", "mpn": "mpn", "mfr part number": "mpn",
    "alternate make 1": "alternate_make_1", "alternate mpn 1": "alternate_mpn_1", "alternate make 2": "alternate_make_2", "alternate mpn 2": "alternate_mpn_2",
    "quantity": "quantity_per_assembly", "qty": "quantity_per_assembly", "qty per assembly": "quantity_per_assembly", "quantity per assembly": "quantity_per_assembly", "qty/assy": "quantity_per_assembly",
}
##Standardize column names
def canonical_column(name):
    key = re.sub(r"[\s_\-]+", " ", str(name).strip().casefold())
    return ALIASES.get(key)

#Cleans text values whitespaces
def clean_text(value):
    if value is None: return None
    text = str(value).strip()
    return re.sub(r"\s+", " ", text) if text else None

def split_candidates(value):
    """Split explicit multiline/semicolon/pipe candidates; preserve comma in manufacturer text."""
    if value is None: return []
    seen, result = set(), []
    for part in re.split(r"[\r\n;|]+", str(value)):
        text = clean_text(part)
        if text and text.casefold() not in seen:
            seen.add(text.casefold()); result.append(text)
    return result

def normalize_bom(rows):
    """Normalize source row dicts without losing their original private field values."""
    normalized = []
    for number, row in enumerate(rows, 1):
        mapped = {}
        for key, value in row.items():
            standard = canonical_column(key)
            if standard and standard not in mapped: mapped[standard] = value
        mpns = split_candidates(mapped.get("mpn"))
        manufacturers = split_candidates(mapped.get("manufacturer"))
        # Keep manufacturer-MPN pairs together, especially for explicit alternates.
        part_options = []
        if mpns:
            for mpn in mpns:
                part_options.append({"role": "primary", "manufacturer": manufacturers[0] if len(manufacturers) == 1 else None, "mpn": mpn})
        for n in (1, 2):
            alternate_mpns = split_candidates(mapped.get(f"alternate_mpn_{n}"))
            alternate_make = clean_text(mapped.get(f"alternate_make_{n}"))
            for alternate_mpn in alternate_mpns:
                part_options.append({"role": f"alternate_{n}", "manufacturer": alternate_make, "mpn": alternate_mpn})
        all_mpns = list(dict.fromkeys(p["mpn"] for p in part_options))
        normalized.append({
            "item_id": f"ITEM-{number:03d}",
            "source_row": number + 1,
            "source_id": clean_text(mapped.get("source_id")),
            "description": clean_text(mapped.get("description")),
            "manufacturer": manufacturers[0] if len(manufacturers) == 1 else None,
            "manufacturer_candidates": manufacturers,
            "mpn_candidates": all_mpns,
            "part_options": part_options,
            "quantity_per_assembly": mapped.get("quantity_per_assembly"),
            "resolution_status": "multiple_candidates" if len(all_mpns)>1 else "unresolved",
            "approved_equivalence": "unknown",
            "source_values": dict(row),
        })
    return normalized
