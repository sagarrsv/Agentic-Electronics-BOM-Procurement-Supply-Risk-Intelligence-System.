"""Lightweight input guards and deterministic enforcement of Gemini intent labels."""


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


def route_intent(intent):
    """Only gate unsafe/out-of-scope queries; never decide tool selection."""
    if intent == "OUT_OF_SCOPE":
        return intent, "I specialize in electronics BOM analysis, component sourcing, and procurement risk."
    if intent == "UNSAFE":
        return intent, "I can't provide credentials or help bypass access controls. I can help with authorized BOM analysis."
    return "ALLOWED", None

# Execution-budget policy applies to every current and future tool.
MAX_TOOL_ROUNDS = 3
MAX_TOOL_CALLS = 5
MAX_TRANSIENT_ATTEMPTS = 2  # Across LLM requests, independent of HTTP retries.


def _turn_messages(messages):
    """Only inspect this user turn; prior checkpointed conversation is irrelevant."""
    start = 0
    for i, message in enumerate(messages):
        if getattr(message, "type", None) == "human":
            start = i + 1
    return messages[start:]


def _call_key(call):
    """Stable tool + arguments identity. No supplier responses are retained."""
    import json
    return (call.get("name"), json.dumps(call.get("args") or {}, sort_keys=True, default=str))


def _tool_failure(message):
    """Only explicitly marked failures are failures, not empty-but-valid answers."""
    import json
    if getattr(message, "status", None) == "error":
        return True
    content = getattr(message, "content", None)
    try:
        payload = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("status") in ("error", "failed", "api_error")


def _transient_failure(message):
    """Permit agent-level retry only for explicitly transient failures."""
    import json
    content = getattr(message, "content", None)
    try:
        payload = json.loads(content) if isinstance(content, str) else content
    except (TypeError, ValueError):
        return False
    return (isinstance(payload, dict)
            and payload.get("retryable") is True
            and _tool_failure(message))


def check_tool_budget(messages, proposed_calls):
    """Return (allowed, reason) before ToolNode can execute proposed calls.

    Protects all tools, permits new useful calls up to configured budget,
    blocks repeat successes, and bounds *explicitly transient* retries.
    """
    history = _turn_messages(messages)
    previous_calls = [c for m in history for c in (getattr(m, "tool_calls", None) or [])]
    rounds = sum(bool(getattr(m, "tool_calls", None)) for m in history)
    if rounds >= MAX_TOOL_ROUNDS:
        return False, "Tool execution round limit reached"
    if len(previous_calls) + len(proposed_calls) > MAX_TOOL_CALLS:
        return False, "Tool execution call limit reached"

    tool_results = {
        getattr(m, "tool_call_id", None): m for m in history
        if getattr(m, "type", None) == "tool"
    }
    previous_by_key = {}
    for call in previous_calls:
        previous_by_key.setdefault(_call_key(call), []).append(call)

    proposed_keys = [_call_key(c) for c in proposed_calls]
    if len(proposed_keys) != len(set(proposed_keys)):
        return False, "Duplicate tool calls in a single model response"

    for call in proposed_calls:
        key = _call_key(call)
        # Live sourcing is never retried automatically: each request is expensive
        # and the results must not be cached. A user may request another run.
        if call.get("name") == "procure_tool" and any(
            old.get("name") == "procure_tool" for old in previous_calls
        ):
            return False, "Procurement was already requested in this user turn"
        old_calls = previous_by_key.get(key, [])
        if not old_calls:
            continue
        if len(old_calls) >= MAX_TRANSIENT_ATTEMPTS:
            return False, "Repeated identical tool call exceeded retry limit"
        for old in old_calls:
            result = tool_results.get(old.get("id"))
            if result is None or not _transient_failure(result):
                return False, "Repeated tool call has already completed or is not retryable"
    return True, None
