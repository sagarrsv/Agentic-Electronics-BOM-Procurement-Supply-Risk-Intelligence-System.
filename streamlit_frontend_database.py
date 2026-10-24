import streamlit as st
from langchain_core.messages import HumanMessage, AIMessage
from langgraph_backend_database import (
    chatbot,
    llm,
    retrieve_all_threads,
    get_thread_titles,
    save_thread_title,
)

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
        update_thread_title_for_first_turn(user_input, ai_message)



