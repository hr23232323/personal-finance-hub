"""The advisor. A model/provider-agnostic tool-calling loop.

Speaks the OpenAI-compatible API, so LLM_BASE_URL can be OpenRouter (default),
OpenAI, a local Ollama, or your own proxy — no code change, just config.

Privacy: only the user's question and the RESULTS of read-only queries the model
chooses to run are sent to the endpoint. There is no tool that can write data.
"""
import datetime
import json

from openai import OpenAI

from . import config, queries

# The model can only ever call these read-only functions.
TOOL_FUNCS = {
    "list_accounts": queries.list_accounts,
    "get_transactions": queries.get_transactions,
    "get_summary": queries.get_summary,
    "spending_by_category": queries.spending_by_category,
    "spending_by_month": queries.spending_by_month,
    "top_merchants": queries.top_merchants,
}

_DATE = {"type": "string", "description": "ISO date YYYY-MM-DD (optional)"}

TOOLS = [
    {"type": "function", "function": {
        "name": "list_accounts",
        "description": "List all accounts with balance, type, and transaction count.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "get_summary",
        "description": "Totals for a period: income, spending, net, count.",
        "parameters": {"type": "object", "properties": {"start": _DATE, "end": _DATE}},
    }},
    {"type": "function", "function": {
        "name": "spending_by_category",
        "description": "Total spending grouped by category, largest first.",
        "parameters": {"type": "object", "properties": {"start": _DATE, "end": _DATE}},
    }},
    {"type": "function", "function": {
        "name": "spending_by_month",
        "description": "Income vs spending per month.",
        "parameters": {"type": "object", "properties": {"start": _DATE, "end": _DATE}},
    }},
    {"type": "function", "function": {
        "name": "top_merchants",
        "description": "Merchants/payees you spent the most at.",
        "parameters": {"type": "object", "properties": {
            "start": _DATE, "end": _DATE,
            "limit": {"type": "integer", "description": "How many (default 10)"}}},
    }},
    {"type": "function", "function": {
        "name": "get_transactions",
        "description": "List individual transactions matching filters, newest first.",
        "parameters": {"type": "object", "properties": {
            "start": _DATE, "end": _DATE,
            "category": {"type": "string"},
            "search": {"type": "string", "description": "Match payee/description text"},
            "min_amount": {"type": "number"}, "max_amount": {"type": "number"},
            "limit": {"type": "integer"}}},
    }},
]


def _system_prompt() -> str:
    today = datetime.date.today().isoformat()
    return (
        f"You are a sharp, candid personal finance advisor. Today is {today}.\n"
        "The user's real transaction data lives in a local database you can query "
        "with the provided read-only tools. ALWAYS call a tool to get real numbers "
        "before making any claim — never invent or estimate figures. Amounts are "
        "signed: positive = money in, negative = money out. Transactions categorized "
        "'Transfers' are internal moves between the user's own accounts (savings "
        "transfers, credit-card payments) — don't count them as spending or income.\n"
        "Be concise and direct. Cite actual dollar figures and dates from the tools. "
        "Proactively point out patterns, wasteful or recurring spending, and things "
        "worth questioning — like a good advisor reviewing someone's statements. "
        "If the data can't answer something, say so plainly."
    )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


def complete_json(system: str, user: str, schema: dict, max_tokens: int = 10000) -> dict:
    """Structured output: ask for a JSON schema and return the parsed object.

    Uses the OpenAI-compatible `response_format` when the provider supports it,
    and falls back to plain JSON parsing when it doesn't.
    """
    if not config.llm_configured():
        raise RuntimeError("No LLM key set.")
    client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)
    kwargs = dict(
        model=config.LLM_MODEL, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    try:
        resp = client.chat.completions.create(**kwargs, response_format={
            "type": "json_schema",
            "json_schema": {"name": "insights", "strict": True, "schema": schema},
        })
    except Exception:
        resp = client.chat.completions.create(**kwargs)  # provider lacks structured output
    return json.loads(_strip_fences(resp.choices[0].message.content or "{}"))


def complete(system: str, user: str, max_tokens: int = 10000) -> str:
    """One-shot completion (no tools). Used by the coach tier to narrate facts.

    The budget is deliberately large: reasoning models (Gemini, o-series, ...)
    spend most of it on hidden reasoning before writing a word, and a truncated
    read is far worse than a few extra tokens.
    """
    if not config.llm_configured():
        raise RuntimeError("No LLM key set.")
    client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)
    resp = client.chat.completions.create(
        model=config.LLM_MODEL, max_tokens=max_tokens,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    return (resp.choices[0].message.content or "").strip()


def chat(message: str, history: list | None = None, max_rounds: int = 6) -> str:
    if not config.llm_configured():
        raise RuntimeError(
            "No LLM key set. Add LLM_API_KEY to your .env (OpenRouter by default) "
            "to use the advisor. Everything else works without it."
        )

    client = OpenAI(base_url=config.LLM_BASE_URL, api_key=config.LLM_API_KEY)
    messages = [{"role": "system", "content": _system_prompt()}]
    messages += history or []
    messages.append({"role": "user", "content": message})

    for _ in range(max_rounds):
        resp = client.chat.completions.create(
            model=config.LLM_MODEL, messages=messages, tools=TOOLS, tool_choice="auto",
        )
        msg = resp.choices[0].message

        assistant = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        messages.append(assistant)

        if not msg.tool_calls:
            return msg.content or ""

        for tc in msg.tool_calls:
            fn = TOOL_FUNCS.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = fn(**args) if fn else {"error": "unknown tool"}
            except Exception as e:  # surface tool errors to the model, don't crash
                result = {"error": str(e)}
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, default=str)})

    return "I looked into it but couldn't converge on an answer — try narrowing the question."
