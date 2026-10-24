import streamlit as st
from langgraph_backend import chatbot
from langchain_core.messages import HumanMessage, AIMessage
import uuid


# --------------------------------- util functions ------------------------------------- #

def generate_thread_id():
    return str(uuid.uuid4())[-15:]


def first_chat_summary(user_input, ai_response):
    summary_prompt = (
        "Generate a summary of the conversation in exactly 5 to 10 words. "
        "Return only the summary text and nothing else.\n\n"
        f"User: {user_input}\nAssistant: {ai_response}"
    )

    # this is only for display label, not for backend thread persistence
    result = chatbot.invoke(
        {"messages": [HumanMessage(content=summary_prompt)]},
        config={"configurable": {"thread_id": st.session_state["thread_id"]}},
    )

    summary = result["messages"][-1].text.strip()

    if len(summary.split()) > 10:
        summary = " ".join(summary.split()[:10])

    return summary


def reset_chat():
    st.session_state["thread_id"] = generate_thread_id()
    st.session_state["thread_title"] = str(st.session_state["thread_id"])
    add_threads(st.session_state["thread_id"])
    st.session_state["message_history"] = []


def add_threads(thread_id):
    if thread_id not in st.session_state["chat_threads"]:
        st.session_state["chat_threads"].append(thread_id)

    if thread_id not in st.session_state["thread_titles"]:
        st.session_state["thread_titles"][thread_id] = str(thread_id)


def load_conversation(thread_id):
    state = chatbot.get_state(config={"configurable": {"thread_id": thread_id}})
    values = getattr(state, "values", {}) or {}
    return values.get("messages", [])


# --------------------------------- session state ------------------------------------- #

if "message_history" not in st.session_state:
    st.session_state["message_history"] = []

if "thread_id" not in st.session_state:
    st.session_state["thread_id"] = generate_thread_id()

if "thread_title" not in st.session_state:
    st.session_state["thread_title"] = str(st.session_state["thread_id"])

if "chat_threads" not in st.session_state:
    st.session_state["chat_threads"] = []

if "thread_titles" not in st.session_state:
    st.session_state["thread_titles"] = {}

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

for message in st.session_state["message_history"]:
    with st.chat_message(message["role"]):
        st.text(message["content"])

user_input = st.chat_input("Type your message here...")

if user_input:
    is_first_turn = len(st.session_state["message_history"]) == 0

    st.session_state["message_history"].append({"role": "user", "content": user_input})

    with st.chat_message("user"):
        st.text(user_input)

    CONFIG = {"configurable": {"thread_id": st.session_state["thread_id"]}}

    with st.chat_message("assistant"):
        ai_message = st.write_stream(
            message_chunk.text
            for message_chunk, metadata in chatbot.stream(
                {"messages": [HumanMessage(content=user_input)]},
                config=CONFIG,
                stream_mode="messages",
            )
        )

    st.session_state["message_history"].append({"role": "assistant", "content": ai_message})

    if is_first_turn:
        summary = first_chat_summary(user_input, ai_message)
        display_label = summary[:80]

        st.session_state["thread_titles"][st.session_state["thread_id"]] = display_label
        st.session_state["thread_title"] = display_label
