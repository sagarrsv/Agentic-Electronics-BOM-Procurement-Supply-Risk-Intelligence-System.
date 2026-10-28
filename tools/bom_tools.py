

"""Deterministic queries on the BOM attached to a conversation."""

from bom.save_database import get_bom


# Simple categories inferred from descriptions.
# These are not manufacturer-verified classifications.
CATEGORIES = {
    "resistor": ("resistor",),
    "capacitor": ("capacitor",),
    "mosfet": ("mosfet",),
    "diode": ("diode",),
    "inductor": ("inductor",),
    "connector": ("connector",),
    "transistor": ("transistor",),
    "regulator": ("regulator",),
}


def category_of(item):
    """Classify a component using explicit description keywords."""
    description = (item.get("description") or "").casefold()

    for category, keywords in CATEGORIES.items():
        if any(word in description for word in keywords):
            return category

    return "unknown"


def matches(item, search_term):
    """Search descriptions, inferred categories, and manufacturers."""
    term = search_term.strip().casefold()

    # Recognize simple plurals: capacitors -> capacitor.
    if term.endswith("s") and term[:-1] in CATEGORIES:
        term = term[:-1]

    values = [
        item.get("description") or "",
        category_of(item),
    ]
    values += item.get("manufacturer_candidates", [])
    values += [p.get("manufacturer") or "" for p in item.get("part_options", [])]

    return any(term in value.casefold() for value in values)


def present_item(item):
    """Return relevant fields, omitting original private source values."""
    return {
        "item_id": item["item_id"],
        "description": item.get("description"),
        "manufacturer_candidates": item.get("manufacturer_candidates", []),
        "mpn_candidates": item.get("mpn_candidates", []),
        "part_options": item.get("part_options", []),
        "quantity_per_assembly": item.get("quantity_per_assembly"),
    }


def bom_query(operation, thread_id, search_term=None, production_quantity=None):
    """Query the uploaded BOM using one of six supported operations."""

    allowed = {
        "summary", "filter", "lookup",
        "quantity", "ambiguities", "validation_issues"
    }

    if operation not in allowed:
        return {"error": "Unsupported operation"}

    # Read only the BOM belonging to the current chat.
    bom = get_bom(thread_id)

    if bom is None:
        return {"error": "No BOM uploaded for this conversation"}

    items = bom["items"]

    # 1. Summarize components, categories and validation counts.
    if operation == "summary":
        categories = {}

        for item in items:
            category = category_of(item)
            categories[category] = categories.get(category, 0) + 1

        return {
            "summary": bom["summary"],
            "categories": categories
        }

    # 2. Find rows with multiple listed options or manufacturer candidates.
    if operation == "ambiguities":
        found = [
            item for item in items
            if len(item.get("mpn_candidates", [])) > 1
            or len(item.get("manufacturer_candidates", [])) > 1
        ]

        return {
            "count": len(found),
            "items": [present_item(item) for item in found]
        }

    # 3. Retrieve validation warnings and errors.
    if operation == "validation_issues":
        return {
            "count": len(bom["issues"]),
            "issues": bom["issues"]
        }

    # The remaining operations require a search term.
    if not isinstance(search_term, str) or not search_term.strip():
        return {"error": "search_term is required for this operation"}

    # 4. Exact MPN lookup across all candidates.
    if operation == "lookup":
        term = search_term.strip().casefold()

        found = [
            item for item in items
            if any(
                mpn.casefold() == term
                for mpn in item.get("mpn_candidates", [])
            )
        ]

        return {
            "count": len(found),
            "items": [present_item(item) for item in found]
        }

    # Search descriptions, categories or manufacturer candidates.
    found = [item for item in items if matches(item, search_term)]

    # 5. Return matching components.
    if operation == "filter":
        return {
            "count": len(found),
            "items": [present_item(item) for item in found]
        }

    # 6. Calculate requirements for a production quantity.
    if (
        isinstance(production_quantity, bool)
        or not isinstance(production_quantity, int)
        or production_quantity <= 0
    ):
        return {"error": "production_quantity must be a positive integer"}

    # Invalid BOM quantities must not contribute to the total.
    valid = [
        item for item in found
        if isinstance(item.get("quantity_per_assembly"), int)
        and not isinstance(item["quantity_per_assembly"], bool)
        and item["quantity_per_assembly"] > 0
    ]

    invalid = [
        item["item_id"] for item in found
        if item not in valid
    ]

    return {
        "production_quantity": production_quantity,
        "matched_items": len(found),
        "quantities": [
            {
                "item_id": item["item_id"],
                "description": item.get("description"),
                "required_quantity":
                    item["quantity_per_assembly"] * production_quantity
            }
            for item in valid
        ],
        "total_required_units": sum(
            item["quantity_per_assembly"] * production_quantity
            for item in valid
        ),
        "excluded_invalid_quantity_items": invalid,
        "note": (
            "Total is units across matched BOM rows, "
            "not one interchangeable component or purchase quantity."
        )
    }
