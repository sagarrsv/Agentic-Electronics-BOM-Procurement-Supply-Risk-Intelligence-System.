"""Structural and row-level BOM validation."""
from decimal import Decimal, InvalidOperation
from .normalizer import canonical_column

class BOMFormatError(ValueError):
    pass

def validate_bom(rows, columns=None):
    """Return validated records and warnings; invalid quantities are errors, not coerced."""
    rows = [dict(item) for item in rows]
    if columns is not None:
        mapped = {canonical_column(c) for c in columns}
        missing = {"description", "mpn", "quantity_per_assembly"} - mapped
        if missing: raise BOMFormatError("Missing required columns: " + ", ".join(sorted(missing)))
    issues = []
    for item in rows:
        row = item["source_row"]
        val = item["quantity_per_assembly"]
        try:
            if val is None or str(val).strip() == "": raise InvalidOperation
            number = Decimal(str(val).strip())
            if not number.is_finite() or number <= 0 or number != number.to_integral_value(): raise InvalidOperation
            item["quantity_per_assembly"] = int(number)
        except (InvalidOperation, ValueError, TypeError):
            item["quantity_per_assembly"] = None
            issues.append({"row": row, "level": "error", "field": "quantity_per_assembly", "message": "Expected a positive whole number"})
        if not item["description"]:
            issues.append({"row": row, "level": "warning", "field": "description", "message": "Missing component description"})
        if not item["mpn_candidates"]:
            issues.append({"row": row, "level": "warning", "field": "mpn_candidates", "message": "Missing manufacturer part number"})
        if len(item["mpn_candidates"]) > 1:
            issues.append({"row": row, "level": "warning", "field": "mpn_candidates", "message": "Multiple candidates; equivalence not verified"})
        if not item["manufacturer_candidates"]:
            issues.append({"row": row, "level": "warning", "field": "manufacturer", "message": "Manufacturer not provided"})
        elif len(item["manufacturer_candidates"]) > 1:
            issues.append({"row": row, "level": "warning", "field": "manufacturer", "message": "Multiple manufacturer candidates; mapping to MPNs unverified"})
    return rows, issues
