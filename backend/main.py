import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import config
from routers import stock, securities as sec_router
from services import db, securities
from services.rate_limit import RateLimitMiddleware

logging.basicConfig(level=logging.INFO)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app):
    db.init_db(config.DB_PATH)
    threading.Thread(target=securities.ensure_securities, daemon=True).start()
    yield
    db.shutdown()


app = FastAPI(lifespan=lifespan)
app.add_middleware(RateLimitMiddleware)
app.include_router(stock.router, prefix="/api")
app.include_router(sec_router.router, prefix="/api")
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
