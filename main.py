import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

import store.models  # noqa: F401  (registers Phase 4/5 tables BEFORE init_db runs)
from store.database import init_db
from api.errors import register_error_handlers
from api import rules, cameras, captures

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()   # creates any missing tables in vigil.db; existing data is untouched
    yield


app = FastAPI(title="Vigil Rule Compiler", version="0.6.0", lifespan=lifespan)
register_error_handlers(app)
app.include_router(rules.router)
app.include_router(cameras.router)
app.include_router(captures.router)


@app.get("/health")
def health():
    return {"status": "ok"}