"""Thread-scoped bridge: saved BOM -> exact original MPN sourcing -> deterministic risk."""
from bom.save_database import get_bom
from tools.sourcing_multi import source_bom_two_suppliers
from tools.risk import evaluate_report
from langsmith import traceable

@traceable(
    name="Procurement Pipeline",
    run_type="chain"
)
def sourcing_rows(bom):
    """Fail closed on missing/multiple primary identities. Never auto-pick alternates."""
    if bom is None:
        raise ValueError("No BOM uploaded in this conversation")
    if bom["summary"].get("errors"):
        raise ValueError("BOM contains quantity validation errors; correct and reupload")
    rows = []
    for item in bom["items"]:
        options = [o for o in item.get("part_options", []) if o.get("role") == "primary"]
        makers = item.get("manufacturer_candidates") or []
        if len(options) != 1 or len(makers) != 1 or not options[0].get("mpn") or not options[0].get("manufacturer"):
            raise ValueError(f"{item['item_id']}: ambiguous/missing original primary MPN or manufacturer")
        qty = item.get("quantity_per_assembly")
        if type(qty) is not int or qty <= 0:
            raise ValueError(f"{item['item_id']}: invalid assembly quantity")
        rows.append({"item_id": item["item_id"], "MAKE": options[0]["manufacturer"],
                     "MPN": options[0]["mpn"], "quantity_per_assembly": qty})
    if not rows:
        raise ValueError("BOM is empty")
    return rows


# Transient hand-off for this Streamlit interaction only. No disk/SQLite writes.
# The frontend consumes and removes it immediately after LangGraph finishes.
from threading import Lock
_LIVE_REPORTS = {}
_LIVE_LOCK = Lock()


def clear_live_report(thread_id):
    with _LIVE_LOCK:
        _LIVE_REPORTS.pop(thread_id, None)


def take_live_report(thread_id):
    """Remove and return a report once. Never persist this information."""
    with _LIVE_LOCK:
        return _LIVE_REPORTS.pop(thread_id, None)


def source_thread(thread_id, assemblies, force_refresh=False, progress=None):
    """Always query live suppliers; no cross-request lookup cache."""
    if type(assemblies) is not int or assemblies < 1:
        raise ValueError("assemblies must be a positive whole number")
    if progress:
        progress("bom_loading")
    rows = sourcing_rows(get_bom(thread_id))
    if progress:
        progress("bom_validated", total=len(rows))
    clear_live_report(thread_id)
    phase5 = source_bom_two_suppliers(rows, assemblies, verbose=False, progress=progress)
    if progress:
        progress("risk_started")
    phase6 = evaluate_report(phase5)
    if progress:
        progress("risk_completed", total=len(phase6.get("bom_coverage", {}).get("component_rows", [])))
    # Do not retain the Phase 5 response; only the report to be displayed now.
    with _LIVE_LOCK:
        _LIVE_REPORTS[thread_id] = phase6
    return {"status": "ready", "assemblies": assemblies}


def compact_evidence(report):
    """Explicit local/UI use only; never return this via a checkpointed LangGraph tool."""
    coverage = report["bom_coverage"]
    return {"assemblies": report["assemblies"], "risk_summary": report["summary"],
            "coverage": {k: v for k, v in coverage.items() if k != "component_rows"},
            "component_rows": coverage["component_rows"]}


def narrative_evidence(thread_id):
    """Brief derived findings for a request-scoped Gemini call; no raw offer payload."""
    with _LIVE_LOCK:
        report = _LIVE_REPORTS.get(thread_id)
        if report is None:
            return None
        items = report.get("items", [])
        coverage = report.get("bom_coverage", {})
        return {
            "assemblies": report["assemblies"],
            "risk_distribution": report["summary"]["risk_levels"],
            "assemblies_supported_by_known_stock": coverage.get("assemblies_supported_by_reported_stock"),
            "coverage_is_conservative": True,
            "stock_gaps": [
                {"item_id": row["item_id"], "mpn": row["mpn"],
                 "required": row["required_quantity"],
                 "known_combined_stock": row["combined_known_reported_stock"],
                 "gap_beyond_known_stock": row["units_needed_beyond_known_stock"],
                 "shortage_status": row["shortage_status"],
                 "unknown_stock_suppliers": row["stock_unknown_suppliers"]}
                for row in coverage.get("component_rows", [])
                if row["units_needed_beyond_known_stock"] > 0
            ],
            "attention_items": [
                {"item_id": x["item_id"], "mpn": x["mpn"],
                 "risk": x["overall_risk_level"],
                 "availability": x["inventory"]["availability_status"],
                 "known_shortfall": x["inventory"]["known_stock_shortfall"],
                 "stock_unknown": bool(x["inventory"]["unknown_stock_suppliers"]),
                 "lifecycle": x["lifecycle"],
                 "lead_time_status": x["lead_time_status"],
                 "moq_status": x["moq_status"],
                 "data_gaps": x["unknown_fields"]}
                for x in items if x["overall_risk_level"] in ("critical", "high")
            ],
            "important_notes": [
                "Unknown stock must never be described as zero available.",
                "Replenishment lead time is not a confirmed delivery date.",
                "No unapproved manufacturer/MPN substitutions are permitted.",
            ],
        }
