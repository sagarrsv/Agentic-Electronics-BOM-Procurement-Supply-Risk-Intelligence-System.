"""Minimal deterministic safety checks. Domain intent is decided by the existing Gemini router."""


def basic_query_check(question):
    """Reject empty input; avoid brittle keyword-based domain classification."""
    if not isinstance(question, str) or not question.strip():
        return "Please enter a question about your BOM or procurement analysis."
    return None


def procurement_call_already_made(messages):
    """Prevent duplicate procurement requests after the latest human message."""
    for msg in reversed(messages):
        if getattr(msg, "type", None) == "human":
            break
        if getattr(msg, "type", None) == "tool" and getattr(msg, "name", "") == "procure_tool":
            return True
        for call in getattr(msg, "tool_calls", []) or []:
            if call.get("name") == "procure_tool":
                return True
    return False


def supplier_failure_summary(phase5):
    """Distinguish a supplier transport failure from a negative catalog match."""
    failures = []
    for item in phase5.get("items", []):
        for result in item.get("supplier_results", []):
            if result.get("lookup_status") == "api_error":
                failures.append({"item_id": item.get("item_id"), "supplier": result.get("supplier")})
    return {"supplier_api_failures": failures, "has_partial_supplier_failures": bool(failures)}
