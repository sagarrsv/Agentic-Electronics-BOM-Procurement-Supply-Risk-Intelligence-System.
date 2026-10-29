import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage
from langgraph_backend_database import (
    chatbot,
    llm,
    retrieve_all_threads,
    get_thread_titles,
    save_thread_title,
)
# BOM parsing and database operations
from bom import parse_bom
from bom.save_database import save_bom, get_bom
from tools.procurement_tools import take_live_report, clear_live_report
from bom.validator import BOMFormatError

import uuid
import pandas as pd


# --------------------------------- util functions ------------------------------------- #

def generate_thread_id():
    return str(uuid.uuid4())[-15:]


def first_chat_summary(user_input, ai_response):
    summary_prompt = (
        "Generate a summary of the conversation in exactly 3 to 6 words. "
        "Return only the summary text and nothing else.\n\n"
        f"User: {user_input}\nAssistant: {ai_response}"
    )

    # using a seperate llm as the summary thread shouldnt be displayed on ui
    result = llm.invoke(
        [HumanMessage(content=summary_prompt)]
    )
    summary = result.text.strip()

    if len(summary.split()) > 10:
        summary = " ".join(summary.split()[:10])

    return summary


def reset_chat():
    st.session_state["thread_id"] = generate_thread_id()
    st.session_state["thread_title"] = str(st.session_state["thread_id"])

    add_threads(st.session_state["thread_id"])
    st.session_state["message_history"] = []


def add_threads(thread_id):
    threads = st.session_state["chat_threads"]

    # avoid duplicates
    if thread_id in threads:
        threads.remove(thread_id)

    # newest first
    threads.insert(0, thread_id)


def load_conversation(thread_id):
    state = chatbot.get_state(config={"configurable": {"thread_id": thread_id}})
    values = getattr(state, "values", {}) or {}
    return values.get("messages", [])

def update_thread_title_for_first_turn(user_input, ai_message):
    title = first_chat_summary(user_input, ai_message)
    title = title[:80]

    st.session_state["thread_titles"][st.session_state["thread_id"]] = title
    st.session_state["thread_title"] = title

    #save the title in database with thread title table 
    save_thread_title(st.session_state["thread_id"], title)

    # remove old thread it and replace with new
    if st.session_state["thread_id"] in st.session_state["chat_threads"]:
        st.session_state["chat_threads"].remove(st.session_state["thread_id"])

    # ensure the order of chat threads are shwoing recent chat at top
    st.session_state["chat_threads"].insert(0, st.session_state["thread_id"])
    # Avoid rerunning here: that would erase a request-scoped live report.
# --------------------------------- session state ------------------------------------- #

if "message_history" not in st.session_state:
    st.session_state["message_history"] = []

if "thread_id" not in st.session_state:
    st.session_state["thread_id"] = generate_thread_id()

if "thread_title" not in st.session_state:
    st.session_state["thread_title"] = str(st.session_state["thread_id"])

if "chat_threads" not in st.session_state:
    st.session_state["chat_threads"] = list(get_thread_titles().keys())

if "thread_titles" not in st.session_state:
    st.session_state["thread_titles"] = get_thread_titles()

add_threads(st.session_state["thread_id"])


# ------------------------------------- sidebar ---------------------------------------- #

st.sidebar.title("Chatbot Configuration")

if st.sidebar.button("New Chat"):
    reset_chat()

st.sidebar.header("Conversation History")

for thread_id in st.session_state["chat_threads"]:
    label = st.session_state["thread_titles"].get(thread_id, str(thread_id))

    if st.sidebar.button(str(label)):
        st.session_state["thread_id"] = thread_id
        st.session_state["thread_title"] = label

        loaded_messages = load_conversation(thread_id)
        temp_message_history = []

        for message in loaded_messages:
            if isinstance(message, HumanMessage):
                role = "user"
            elif isinstance(message, AIMessage):
                if message.tool_calls:
                    continue
                role = "assistant"
            else:
                continue

            temp_message_history.append({"role": role, "content": message.text})

        clear_live_report(thread_id)
        st.session_state["message_history"] = temp_message_history


# ------------------------------------- main ui ----------------------------------------- #

# ------------------------ BOM upload ------------------------ #

st.subheader("BOM Analysis")

# Link the uploaded BOM to the currently selected conversation.
thread_id = st.session_state["thread_id"]

with st.form(f"bom_upload_{thread_id}"):
    uploaded_file = st.file_uploader(
        "Upload BOM file",
        type=["csv", "xlsx"]
    )
    upload_clicked = st.form_submit_button("Process BOM")

if upload_clicked:
    if uploaded_file is None:
        st.warning("Please select a BOM file.")
    else:
        try:
            # Parse, normalize, and validate using our Feature 1 code.
            result = parse_bom(uploaded_file)

            # Persist normalized records and issues in SQLite.
            save_bom(thread_id, uploaded_file.name, result)

            clear_live_report(thread_id)
            st.success("BOM uploaded and saved successfully.")

        except BOMFormatError as error:
            # Expected validation failure, e.g. missing columns.
            st.error(f"Invalid BOM: {error}")

        except Exception:
            # Avoid exposing private BOM contents in error messages.
            st.error("Failed to process or save the BOM.")


# ------------------------ BOM summary ------------------------ #

# Load the BOM associated with this conversation, if one exists.
saved_bom = get_bom(thread_id)

if saved_bom:
    summary = saved_bom["summary"]

    st.caption(f"Active BOM: {saved_bom['filename']}")

    col1, col2, col3 = st.columns(3)

    col1.metric("Components", summary["rows"])
    col2.metric("Warnings", summary["warnings"])
    col3.metric("Errors", summary["errors"])

    # Invalid quantities must be corrected before cost calculations.
    if summary["errors"] > 0:
        st.warning(
            "Some rows contain invalid quantities. "
            "Quantity-based analysis requires correction."
        )

# Supplier results are rendered only during the current request, below chat.
st.subheader("Supplier coverage & risk")
assembly_target = st.number_input("Assembly target", min_value=1, value=500, step=1,
                                  key=f"assembly_target_{thread_id}")
st.caption("Live procurement reports are displayed after sourcing, then discarded. No supplier-data downloads or database snapshots.")

st.divider()

for message in st.session_state["message_history"]:
    with st.chat_message(message["role"]):
        if message["role"] == "assistant":
            st.markdown(message["content"])
        else:
            st.text(message["content"])

user_input = st.chat_input("Type your message here...")

if user_input:
    is_first_turn = len(st.session_state["message_history"]) == 0

    st.session_state["message_history"].append({"role": "user", "content": user_input})

    with st.chat_message("user"):
        st.text(user_input)

    CONFIG = {
        "configurable": {"thread_id": st.session_state["thread_id"]},
        "recursion_limit": 10
        }

    
    with st.chat_message("assistant"):

        # Show activity immediately, even before Gemini returns any text.
        status = st.status("Selecting tool...", expanded=True)
        # Render all execution events INSIDE the collapsible status box.
        with status:
            trace_placeholder = st.empty()
        trace_lines = []

        STAGE_LABELS = {
            "bom_loading": "Loading BOM from SQLite",
            "bom_validated": "Validated original MPNs, manufacturers and quantities",
            "digikey_started": "Querying DigiKey catalog",
            "digikey_candidates_ready": "DigiKey candidate searches completed",
            "manufacturer_verification_started": "Checking manufacturer aliases and identities (Gemini where required)",
            "manufacturer_verified": "Manufacturer mapping and DigiKey aliases verified",
            "mouser_started": "Querying Mouser for exact original parts",
            "mouser_progress": "Mouser part lookup completed",
            "mouser_completed": "Mouser sourcing completed",
            "digikey_verified": "DigiKey MPN/manufacturer matches verified",
            "risk_started": "Calculating stock coverage, shortages and risk",
            "risk_completed": "BOM coverage and risk report calculated",
            "summary_started": "Gemini preparing procurement explanation",
            "summary_completed": "Gemini procurement explanation completed",
        }

        def add_trace(line):
            trace_lines.append(line)
            # Only execution metadata (not raw supplier evidence) appears in trace.
            trace_placeholder.markdown("\n\n".join(trace_lines[-16:]))

        def stream_response():
            selected_tools = []

            # Stream both model text and graph execution updates.
            events = chatbot.stream(
                {"messages": [HumanMessage(content=f"[Selected assembly target: {int(assembly_target)}]\n{user_input}")]},
                config=CONFIG,
                stream_mode=["messages", "updates", "custom"]
            )

            for mode, data in events:

                if mode == "custom":
                    if isinstance(data, dict) and data.get("kind") == "procurement_progress":
                        stage = data.get("stage", "")
                        label = STAGE_LABELS.get(stage, stage)
                        completed, total = data.get("completed"), data.get("total")
                        if stage == "mouser_progress":
                            label += f" ({completed}/{total})"
                            # Avoid rendering all 20 identical lines.
                            if completed not in (1, total) and completed % 5:
                                continue
                        elif stage in ("bom_validated", "mouser_completed", "digikey_verified", "risk_completed"):
                            label += f" ({data.get('matched', total)} items)" if stage != "bom_validated" else f" ({total} items)"
                        add_trace("✓ " + label)
                        # Preserve the tool name in the collapsed status title.
                    continue

                if mode == "updates":

                    # LLM completed a node and requested a BOM tool.
                    if "chat_node" in data:
                        response = data["chat_node"]["messages"][-1]

                        if not response.tool_calls and response.content:
                            # Render only final assistant messages, not model text
                            # produced while selecting a tool.
                            yield response.content if isinstance(response.content, str) else response.text

                        if response.tool_calls:
                            operations = [call["name"] for call in response.tool_calls]
                            selected_tools.extend(name for name in operations if name not in selected_tools)

                            add_trace("✓ Tool selected: " + ", ".join(operations))
                            status.update(
                                label=f"Running tool: {', '.join(operations)}",
                                state="running"
                            )

                    # The procurement explanation is produced by summary_node,
                    # not chat_node. Render the final AIMessage from this node.
                    if "summary_node" in data:
                        summary_messages = data["summary_node"].get("messages", [])
                        if summary_messages:
                            answer = summary_messages[-1]
                            if isinstance(answer, AIMessage) and answer.content:
                                yield answer.content if isinstance(answer.content, str) else answer.text

                    # The built-in ToolNode finished execution.
                    if "tools" in data:
                        status.update(
                            label="Tool completed. Preparing answer...",
                            state="running"
                        )

                elif mode == "messages":
                    # Deliberately don't display partial tool-selection text.
                    # Final AI messages are displayed once from node updates.
                    continue

            # Mark completion after the graph finishes.
            status.update(
                label=(
                    "Answered using " + ", ".join(selected_tools)
                    if selected_tools else "Answered without tools"
                ),
                state="complete",
                expanded=False
            )

        ai_message = st.write_stream(stream_response())
        # Keep the trace in the displayed interaction only; it is not
        # added to SQLite conversation messages or session chat history.
        live_report = take_live_report(st.session_state["thread_id"])
        if live_report is not None:
            coverage = live_report["bom_coverage"]
            a, b, c = st.columns(3)
            a.metric("Analyzed target", live_report["assemblies"])
            b.metric("Assemblies supported by known stock", coverage["assemblies_supported_by_reported_stock"])
            c.metric("High / Critical risk items", sum(
                number for level, number in live_report["summary"]["risk_levels"].items()
                if level.lower() in ("high", "critical")))
            rows = coverage["component_rows"]
            table = pd.DataFrame([{
                "Item": r["item_id"], "MPN": r["mpn"], "Required": r["required_quantity"],
                "Mouser stock": r["mouser_reported_stock"],
                "DigiKey stock": r["digikey_reported_stock"],
                "Known combined stock": r["combined_known_reported_stock"],
                "Gap beyond known stock": r["units_needed_beyond_known_stock"],
                "Shortage status": r["shortage_status"],
                "Replenishment days (provisional)": (r["fastest_documented_replenishment_option"] or {}).get("lead_time_days"),
                "Risk": r["overall_risk_level"],
            } for r in rows])
            st.dataframe(table, use_container_width=True, hide_index=True)
            st.caption("Displayed for this interaction only. Unknown stock is not zero; lead time is not a delivery ETA.")
            # Release references promptly; Streamlit's rendered frontend remains visible.
            del live_report, coverage, rows, table



    st.session_state["message_history"].append({"role": "assistant", "content": ai_message})

    if is_first_turn:
        update_thread_title_for_first_turn(user_input, ai_message)



