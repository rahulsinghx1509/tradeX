"""TradeX read-only API.

Run locally:   uvicorn app.main:app --reload     (from the backend folder)
Docs:          http://localhost:8000/docs

There are no order-placement endpoints. Version 1 is analysis only.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import candles, data_quality, health, instruments, market
from app.core.config import get_settings
from app.core.log_setup import setup_logging

setup_logging("%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="TradeX API", version="0.1.0",
              description="Read-only market data API. No order execution.")
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["GET"],
    allow_headers=["*"],
)
for module in (health, instruments, candles, data_quality, market):
    app.include_router(module.router)
