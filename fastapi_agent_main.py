from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List
import pandas as pd
import json
import duckdb
import os
import re
import uuid
import traceback
from pathlib import Path
from openai import OpenAI
import shutil
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend
import matplotlib.pyplot as plt
from helper import get_llm_api_key, load_env

load_env()

# Initialize FastAPI app
app = FastAPI(
    title="CSV Agent API",
    description="An AI agent that can answer general questions and analyze CSV datasets when provided",
    version="1.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# LLM client. Gemini is used through its OpenAI-compatible endpoint, so any
# OpenAI-compatible provider works by changing LLM_BASE_URL and LLM_MODEL.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
MODEL = os.getenv("LLM_MODEL", "gemini-3.5-flash-lite")
client = OpenAI(api_key=get_llm_api_key() or "missing-api-key", base_url=LLM_BASE_URL)

# Cap on rows sent back to the model from a SQL query, to keep prompts small
MAX_RESULT_ROWS = 200

# Directory to store uploaded files and generated charts
UPLOAD_DIR = Path("uploaded_datasets")
UPLOAD_DIR.mkdir(exist_ok=True)

CHARTS_DIR = Path("generated_charts")
CHARTS_DIR.mkdir(exist_ok=True)

# Mount the charts directory to serve static files
app.mount("/charts", StaticFiles(directory=str(CHARTS_DIR)), name="charts")

# Store active sessions (in production, use Redis or a proper database)
active_sessions = {}


# Pydantic models
class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str


class ChatResponse(BaseModel):
    session_id: Optional[str] = None
    response: str
    visualization_code: Optional[str] = None
    chart_urls: Optional[List[str]] = None  # URLs to chart images
    mode: str  # "general" or "data_analysis"


class SessionInfo(BaseModel):
    session_id: str
    filename: str
    columns: List[str]
    row_count: int


# Prompts
SQL_GENERATION_PROMPT = """
Generate a DuckDB SQL query based on a prompt. Do not reply with anything besides the SQL query.
Only write read-only SELECT (or WITH ... SELECT) queries.
The prompt is: {prompt}

The available columns are: {columns}
The table name is: {table_name}
"""

DATA_ANALYSIS_PROMPT = """
Analyze the following data: {data}
Your job is to answer the following question: {prompt}
"""

CREATE_CHART_PROMPT = """
Write python code that creates a clear, well-labelled chart for this goal: {visualization_goal}
Only return the code, no other text.
Use matplotlib and pandas. The data is already loaded as a pandas DataFrame called 'df'
with these columns: {columns}
Here is a preview of the data:
{data}
Pick a suitable chart type, and set a title and axis labels.
The code should create the plot but NOT call plt.show() or plt.savefig().
Just create the figure and plot, we'll handle saving it.
"""

SYSTEM_PROMPT_WITH_DATA = """
You are a helpful assistant that can answer questions about the user's uploaded dataset.
You have access to tools for database lookup, data analysis, and visualization generation.
The user has uploaded a dataset, so you can use the data analysis tools when needed.
Always call lookup_given_dataset before analyze_given_dataset or generate_visualization.
Format answers in Markdown; use tables for tabular results. Charts are shown to the user
automatically below your answer, so do not include image links,
code, or remarks about where the chart is shown.
"""

SYSTEM_PROMPT_GENERAL = """
You are a helpful AI assistant. You can answer general questions, help with various tasks,
and provide information on a wide range of topics.

If the user wants to analyze data, inform them that they can upload a CSV file to enable
data analysis features including SQL queries, data analysis, and visualization generation.
"""


def strip_code_fences(text: str) -> str:
    """Remove Markdown code fences (```sql, ```python, ```) from model output."""
    return re.sub(r"```[a-zA-Z]*", "", text or "").strip()


def friendly_error(e: Exception) -> str:
    """Short, user-facing message for an LLM/provider error."""
    if getattr(e, "status_code", None) == 429 or "RESOURCE_EXHAUSTED" in str(e):
        return ("The AI model's rate limit or daily quota has been reached. "
                "Please wait a bit and try again, or set LLM_MODEL to another model.")
    if getattr(e, "status_code", None) in (401, 403):
        return "The AI provider rejected the API key. Check GEMINI_API_KEY in your .env file."
    return f"Error processing request: {e}"


def is_data_session(session_id: Optional[str]) -> bool:
    session = active_sessions.get(session_id) if session_id else None
    return bool(session and "dataset_path" in session)


# Tool implementations
def generate_sql_query(prompt: str, columns: list, table_name: str) -> str:
    """Generate an SQL query based on a prompt"""
    formatted_prompt = SQL_GENERATION_PROMPT.format(
        prompt=prompt,
        columns=columns,
        table_name=table_name
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": formatted_prompt}],
    )

    return response.choices[0].message.content or ""


def run_sql(sql_query: str, df: pd.DataFrame, table_name: str) -> pd.DataFrame:
    """Run a read-only query against df, registered under table_name."""
    if not re.match(r"^\s*(select|with)\b", sql_query, re.IGNORECASE):
        raise ValueError(f"Only SELECT queries are allowed, got: {sql_query[:80]}")
    con = duckdb.connect()
    try:
        con.register(table_name, df)
        return con.execute(sql_query).df()
    finally:
        con.close()


def lookup_given_dataset(prompt: str, session_id: str) -> str:
    """Implementation of dataset lookup using SQL"""
    try:
        session = active_sessions.get(session_id)
        if not session or "dataset_path" not in session:
            return "Error: No dataset found. Please upload a CSV file first to use data analysis features."

        df = pd.read_csv(session["dataset_path"])
        sql_query = strip_code_fences(generate_sql_query(prompt, df.columns.tolist(), session["table_name"]))
        result = run_sql(sql_query, df, session["table_name"])

        # Keep the result so charts plot the queried data, not the raw file
        session["last_result"] = result

        if len(result) > MAX_RESULT_ROWS:
            return (result.head(MAX_RESULT_ROWS).to_string()
                    + f"\n... ({len(result)} rows total, showing first {MAX_RESULT_ROWS})")
        return result.to_string()
    except Exception as e:
        return f"Error accessing data: {str(e)}"


def analyze_given_dataset(prompt: str, data: str) -> str:
    """Implementation of AI-powered data analysis"""
    formatted_prompt = DATA_ANALYSIS_PROMPT.format(data=data, prompt=prompt)

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": formatted_prompt}],
    )

    analysis = response.choices[0].message.content
    return analysis if analysis else "No analysis could be generated"


def create_chart(data: str, visualization_goal: str, columns: list) -> str:
    """Ask the model for matplotlib code that charts df for the given goal"""
    formatted_prompt = CREATE_CHART_PROMPT.format(
        visualization_goal=visualization_goal,
        columns=columns,
        data=data[:4000],
    )

    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": formatted_prompt}],
    )

    return strip_code_fences(response.choices[0].message.content or "")


def chart_dataframe(session: dict) -> pd.DataFrame:
    """The data a chart should plot: the last query result, else the full dataset."""
    last_result = session.get("last_result")
    if isinstance(last_result, pd.DataFrame) and not last_result.empty:
        return last_result.copy()
    return pd.read_csv(session["dataset_path"])


def execute_visualization_code(code: str, df: pd.DataFrame) -> Optional[str]:
    """Run generated matplotlib code against df and save the figure. Returns its URL."""
    try:
        plt.close("all")
        plt.figure(figsize=(10, 6))
        exec(code, {"df": df, "plt": plt, "pd": pd})

        chart_filename = f"chart_{uuid.uuid4()}.png"
        plt.savefig(CHARTS_DIR / chart_filename, format="png", bbox_inches="tight", dpi=100)
        return f"/charts/{chart_filename}"
    except Exception as e:
        print(f"Error executing visualization code: {str(e)}")
        traceback.print_exc()
        return None
    finally:
        plt.close("all")


def generate_visualization(data: str, visualization_goal: str, session_id: str) -> tuple[str, Optional[str]]:
    """Generate a visualization based on the data and goal, return code and chart URL"""
    session = active_sessions.get(session_id)
    if not session or "dataset_path" not in session:
        return "Error: No dataset found in session", None

    df = chart_dataframe(session)
    code = create_chart(data, visualization_goal, df.columns.tolist())
    chart_url = execute_visualization_code(code, df)
    return code, chart_url


# Tool definitions (only used when a dataset is available)
tools = [
    {
        "type": "function",
        "function": {
            "name": "lookup_given_dataset",
            "description": "Look up data from the uploaded dataset",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The unchanged prompt that the user provided."
                    }
                },
                "required": ["prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_given_dataset",
            "description": "Analyze given data to extract insights",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "description": "The lookup_given_dataset tool's output."
                    },
                    "prompt": {
                        "type": "string",
                        "description": "The unchanged prompt that the user provided."
                    }
                },
                "required": ["data", "prompt"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_visualization",
            "description": "Generate a matplotlib chart from the data returned by lookup_given_dataset",
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "string",
                        "description": "The lookup_given_dataset tool's output."
                    },
                    "visualization_goal": {
                        "type": "string",
                        "description": "The goal of the visualization."
                    }
                },
                "required": ["data", "visualization_goal"]
            }
        }
    }
]


def handle_tool_calls(tool_calls, messages, session_id):
    """Execute tools and append results to messages.

    Returns (messages, chart_urls, visualization_code).
    """
    chart_urls = []
    visualization_code = None

    tool_implementations = {
        "lookup_given_dataset": lambda **kwargs: lookup_given_dataset(session_id=session_id, **kwargs),
        "analyze_given_dataset": analyze_given_dataset,
        "generate_visualization": lambda **kwargs: generate_visualization(session_id=session_id, **kwargs)
    }

    for tool_call in tool_calls:
        name = tool_call.function.name
        try:
            function = tool_implementations[name]
            function_args = json.loads(tool_call.function.arguments or "{}")
            result = function(**function_args)
        except Exception as e:
            result = f"Error running tool {name}: {e}"

        # Visualization returns (code, chart_url)
        if name == "generate_visualization" and isinstance(result, tuple):
            code, chart_url = result
            visualization_code = code
            if chart_url:
                chart_urls.append(chart_url)
                result = f"Chart created successfully. Code used:\n{code}"
            else:
                result = f"Chart could not be created. Code attempted:\n{code}"

        messages.append({
            "role": "tool",
            "content": str(result),
            "tool_call_id": tool_call.id
        })

    return messages, chart_urls, visualization_code


def run_agent_with_data(messages, session_id):
    """Agent with data analysis capabilities"""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]

    if not any(msg.get("role") == "system" for msg in messages):
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT_WITH_DATA})

    visualization_code = None
    all_chart_urls = []

    max_iterations = 10  # Prevent infinite loops

    for iteration in range(1, max_iterations + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                tools=tools,  # type: ignore
            )
            response_msg = response.choices[0].message

            if not response_msg.tool_calls:
                return response_msg.content, visualization_code, all_chart_urls

            # model_dump keeps provider extras (e.g. Gemini thought signatures)
            # that must be sent back on the next turn.
            messages.append(response_msg.model_dump(exclude_none=True))
            messages, chart_urls, code = handle_tool_calls(response_msg.tool_calls, messages, session_id)
            all_chart_urls.extend(chart_urls)
            if code:
                visualization_code = code

        except Exception as e:
            print(f"Error in agent iteration {iteration}: {str(e)}")
            traceback.print_exc()
            return friendly_error(e), visualization_code, all_chart_urls

    return "Maximum iterations reached", visualization_code, all_chart_urls


def run_agent_general(messages):
    """Agent for general conversation without data tools"""
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]

    if not any(msg.get("role") == "system" for msg in messages):
        messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT_GENERAL})

    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=messages,  # type: ignore
        )
    except Exception as e:
        print(f"Error in general agent: {e}")
        return friendly_error(e), None, None

    return response.choices[0].message.content, None, None


# API Endpoints
@app.post("/upload", response_model=SessionInfo)
async def upload_dataset(file: UploadFile = File(...)):
    """Upload a CSV dataset and create a new session"""
    # Keep only the base name so a crafted filename can't escape UPLOAD_DIR
    filename = Path(file.filename or "").name
    if not filename.lower().endswith('.csv'):
        raise HTTPException(status_code=400, detail="Only CSV files are supported")

    session_id = str(uuid.uuid4())
    file_path = UPLOAD_DIR / f"{session_id}_{filename}"

    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        df = pd.read_csv(file_path)
    except Exception as e:
        file_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Could not read CSV file: {str(e)}")

    active_sessions[session_id] = {
        "dataset_path": str(file_path),
        "table_name": f"dataset_{session_id.replace('-', '_')}",
        "filename": filename,
        "columns": df.columns.tolist(),
        "row_count": len(df),
        "messages": []
    }

    return SessionInfo(
        session_id=session_id,
        filename=filename,
        columns=df.columns.tolist(),
        row_count=len(df)
    )


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """Send a message to the agent (works with or without uploaded data)"""
    try:
        if is_data_session(request.session_id):
            session_id = request.session_id
            session = active_sessions[session_id]
            session["messages"].append({"role": "user", "content": request.message})

            response_text, viz_code, chart_urls = run_agent_with_data(
                session["messages"].copy(),
                session_id
            )
            mode = "data_analysis"
        else:
            # Reuse an existing general session so the conversation keeps its memory
            session_id = request.session_id
            if not session_id or session_id not in active_sessions:
                session_id = "general_" + str(uuid.uuid4())
                active_sessions[session_id] = {"messages": []}
            session = active_sessions[session_id]
            session["messages"].append({"role": "user", "content": request.message})

            response_text, viz_code, chart_urls = run_agent_general(session["messages"].copy())
            mode = "general"

        if response_text:
            session["messages"].append({"role": "assistant", "content": response_text})

        return ChatResponse(
            session_id=session_id,
            response=response_text or "I encountered an error processing your request.",
            visualization_code=viz_code,
            chart_urls=chart_urls or None,
            mode=mode
        )
    except Exception as e:
        print(f"Unexpected error in chat endpoint: {str(e)}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing message: {str(e)}")


@app.get("/session/{session_id}", response_model=SessionInfo)
async def get_session(session_id: str):
    """Get information about a session"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = active_sessions[session_id]

    if "dataset_path" not in session:
        raise HTTPException(status_code=400, detail="This is a general conversation session, not a data session")

    return SessionInfo(
        session_id=session_id,
        filename=session["filename"],
        columns=session["columns"],
        row_count=session["row_count"]
    )


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and its associated data"""
    if session_id not in active_sessions:
        raise HTTPException(status_code=404, detail="Session not found")

    session = active_sessions.pop(session_id)
    if "dataset_path" in session:
        Path(session["dataset_path"]).unlink(missing_ok=True)

    return {"message": "Session deleted successfully"}


@app.post("/session/create")
async def create_general_session():
    """Create a new general conversation session without uploading data"""
    session_id = "general_" + str(uuid.uuid4())
    active_sessions[session_id] = {"messages": []}

    return {
        "session_id": session_id,
        "mode": "general",
        "message": "General conversation session created. Upload a CSV file to enable data analysis features."
    }


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "message": "CSV Agent API",
        "version": "1.1.0",
        "model": MODEL,
        "modes": {
            "general": "Chat without data - general AI assistant",
            "data_analysis": "Chat with uploaded CSV - full data analysis capabilities"
        },
        "endpoints": {
            "upload": "POST /upload - Upload a CSV file (optional)",
            "chat": "POST /chat - Chat with the agent (works with or without data)",
            "create_session": "POST /session/create - Create general session",
            "session": "GET /session/{session_id} - Get session info (data sessions only)",
            "delete": "DELETE /session/{session_id} - Delete session",
            "charts": "GET /charts/{filename} - Serve generated chart images"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
