import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import stock, securities as sec_router
from services import db, securities

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app):
    db.init_db("stock.duckdb")
    threading.Thread(target=securities.ensure_securities, daemon=True).start()
    yield
    db.shutdown()


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本番環境では許可オリジンを明示的に指定すること
    allow_methods=["GET"],
    allow_headers=["*"],
)
app.include_router(stock.router, prefix="/api")
app.include_router(sec_router.router, prefix="/api")
