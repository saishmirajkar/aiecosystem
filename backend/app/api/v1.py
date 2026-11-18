"""Aggregates all v1 domain routers."""
from fastapi import APIRouter

from app.modules.erp_core.router import router as erp_router
from app.modules.master_data.router import router as master_router

api_router = APIRouter()
api_router.include_router(erp_router)
api_router.include_router(master_router)
