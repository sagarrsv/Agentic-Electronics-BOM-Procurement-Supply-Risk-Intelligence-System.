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
from bom.validator import BOMFormatError

import uuid


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
    #update the title immediately by rerunning the ui
    st.rerun()
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
                role = "assistant"
            else:
                continue

            temp_message_history.append({"role": role, "content": message.text})

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
        status = st.status("Thinking...", expanded=False)

        def stream_response():
            used_bom_tool = False

            # Stream both model text and graph execution updates.
            events = chatbot.stream(
                {"messages": [HumanMessage(content=user_input)]},
                config=CONFIG,
                stream_mode=["messages", "updates"]
            )

            for mode, data in events:

                if mode == "updates":

                    # LLM completed a node and requested a BOM tool.
                    if "chat_node" in data:
                        response = data["chat_node"]["messages"][-1]

                        if response.tool_calls:
                            used_bom_tool = True
                            operations = [
                                call["args"].get("operation", "unknown")
                                for call in response.tool_calls
                            ]

                            status.update(
                                label=f"Using BOM tool: {', '.join(operations)}",
                                state="running"
                            )

                    # The built-in ToolNode finished execution.
                    if "tools" in data:
                        status.update(
                            label="BOM query completed. Preparing answer...",
                            state="running"
                        )

                elif mode == "messages":
                    message_chunk, metadata = data

                    # Only display Gemini's conversational text.
                    # Tool requests and tool results stay internal.
                    if metadata.get("langgraph_node") == "chat_node":
                        if message_chunk.text:
                            yield message_chunk.text

            # Mark completion after the graph finishes.
            status.update(
                label=(
                    "Answered using BOM tool"
                    if used_bom_tool
                    else "Answered without BOM tools"
                ),
                state="complete",
                expanded=False
            )

        ai_message = st.write_stream(stream_response())



    st.session_state["message_history"].append({"role": "assistant", "content": ai_message})

    if is_first_turn:
        update_thread_title_for_first_turn(user_input, ai_message)



