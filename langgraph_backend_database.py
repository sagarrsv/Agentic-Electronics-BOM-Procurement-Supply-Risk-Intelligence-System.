from langgraph.graph import StateGraph, START, END
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage,HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.message import add_messages
from dotenv import load_dotenv
import sqlite3

load_dotenv()

llm = ChatGoogleGenerativeAI(model = "gemini-3.5-flash-lite")

DB_PATH = "my_ordered_chatbot.db"

class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

def chat_node(state: ChatState) -> ChatState:
    messages = state['messages']
    response = llm.invoke(messages)
    return {'messages': [response]}

graph = StateGraph(ChatState)

graph.add_node('chat_node',chat_node)

###CREATE SQLITE DATABASE#####

conn = sqlite3.connect(database = DB_PATH, check_same_thread=False)
checkpointer = SqliteSaver(conn=conn)

graph.add_edge(START, 'chat_node')
graph.add_edge('chat_node', END)

chatbot = graph.compile(checkpointer=checkpointer)

def retrieve_all_threads():
    all_threads = set()
    for checkpoint in checkpointer.list(None): 
        #Checkpointer list is a generator object and can be looped
        #Checkpoint has many variables and thread ids is found inside config dictionary
        #Number of duplicate thread id's generated for each graph in our case is 3(due to 3 super nodes)
        
        all_threads.add(checkpoint.config['configurable']['thread_id']) 

    return (list(all_threads))




##-----------------Helper frontend functions for saving threads table to sqlite-------------##

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS thread_titles (
            thread_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def get_thread_titles():
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT thread_id, title FROM thread_titles ORDER BY updated_at DESC"
    ).fetchall()
    conn.close()
    return {row[0]: row[1] for row in rows}


# def get_thread_title(thread_id):
#     conn = get_db_connection()
#     row = conn.execute(
#         "SELECT title FROM thread_titles WHERE thread_id = ?",
#         (thread_id,),
#     ).fetchone()
#     conn.close()
#     return row[0] if row else None


def save_thread_title(thread_id, title):
    conn = get_db_connection()
    conn.execute(
        """
        INSERT INTO thread_titles(thread_id, title, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(thread_id)
        DO UPDATE SET title = excluded.title, updated_at = CURRENT_TIMESTAMP
        """,
        (thread_id, title),
    )
    conn.commit()
    conn.close()


