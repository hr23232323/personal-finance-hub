"""FastAPI app: serves the local dashboard and the JSON API.

Binds to 127.0.0.1 only — reachable from your machine, nobody else's.
Run:  python -m app.api      (or)   uvicorn app.api:app --reload
"""
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, db, queries
from .connectors import manual, simplefin

WEB_DIR = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="Personal Finance Hub", lifespan=lifespan)


# ── status / config ──────────────────────────────────────────────────────────
@app.get("/api/status")
def status():
    return {
        "llm_configured": config.llm_configured(),
        "llm_model": config.LLM_MODEL if config.llm_configured() else None,
        "simplefin_connected": bool(db.get_setting(simplefin.SETTING_ACCESS_URL)),
    }


# ── read-only data endpoints (thin wrappers over queries.py) ─────────────────
@app.get("/api/accounts")
def accounts():
    return queries.list_accounts()


@app.get("/api/transactions")
def transactions(start: str = None, end: str = None, account_id: int = None,
                 category: str = None, search: str = None, limit: int = 200):
    return queries.get_transactions(start, end, account_id, category, search, limit=limit)


@app.get("/api/summary")
def summary(start: str = None, end: str = None, account_id: int = None):
    return {
        "summary": queries.get_summary(start, end, account_id),
        "by_category": queries.spending_by_category(start, end, account_id),
        "by_month": queries.spending_by_month(start, end, account_id),
        "top_merchants": queries.top_merchants(start, end, account_id),
    }


# ── manual import (fully local) ──────────────────────────────────────────────
class AccountIn(BaseModel):
    name: str
    type: str = "other"
    institution: str = ""


@app.post("/api/accounts")
def create_account(a: AccountIn):
    account_id = db.create_account(a.name, a.type, a.institution)
    return {"id": account_id}


@app.post("/api/import")
async def import_file(file: UploadFile = File(...), account_id: int = Form(...)):
    data = await file.read()
    try:
        txs = manual.parse_file(file.filename, data)
    except Exception as e:
        raise HTTPException(400, str(e))
    if not txs:
        raise HTTPException(400, "No transactions found in that file.")
    inserted = db.insert_transactions(account_id, txs)
    return {"parsed": len(txs), "inserted": inserted, "skipped_duplicates": len(txs) - inserted}


# ── SimpleFIN sync ───────────────────────────────────────────────────────────
class ConnectIn(BaseModel):
    setup_token: str


@app.post("/api/simplefin/connect")
def simplefin_connect(body: ConnectIn):
    try:
        access_url = simplefin.claim_setup_token(body.setup_token)
    except Exception as e:
        raise HTTPException(400, f"Could not connect: {e}")
    db.set_setting(simplefin.SETTING_ACCESS_URL, access_url)
    return {"connected": True}


class SyncIn(BaseModel):
    start: str = None
    end: str = None


@app.post("/api/simplefin/sync")
def simplefin_sync(body: SyncIn):
    access_url = db.get_setting(simplefin.SETTING_ACCESS_URL)
    if not access_url:
        raise HTTPException(400, "SimpleFIN not connected yet — paste a setup token first.")
    try:
        accts = simplefin.fetch(access_url, body.start, body.end)
    except Exception as e:
        raise HTTPException(502, f"Sync failed: {e}")
    total_new = 0
    for acct in accts:
        account_id = db.get_or_create_source_account(acct)
        total_new += db.insert_transactions(account_id, acct.transactions)
    return {"accounts": len(accts), "inserted": total_new}


# ── advisor chat ─────────────────────────────────────────────────────────────
class ChatIn(BaseModel):
    message: str
    history: list = []


@app.post("/api/chat")
def chat(body: ChatIn):
    from . import llm  # imported lazily so the app runs even without the openai extra configured
    try:
        reply = llm.chat(body.message, body.history)
    except RuntimeError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(502, f"LLM error: {e}")
    return {"reply": reply}


# ── static dashboard (must be mounted last) ──────────────────────────────────
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=WEB_DIR), name="web")


if __name__ == "__main__":
    import argparse
    import os

    import uvicorn

    parser = argparse.ArgumentParser(description="Personal Finance Hub")
    parser.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run("app.api:app", host=args.host, port=args.port, reload=args.reload)
