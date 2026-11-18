"""Aggregates all v1 domain routers."""
from fastapi import APIRouter

from app.modules.erp_core.router import router as erp_router
from app.modules.inventory.reports_router import router as reports_router
from app.modules.inventory.router import router as inventory_router
from app.modules.master_data.router import router as master_router
from app.modules.purchasing.router import router as purchasing_router

api_router = APIRouter()
api_router.include_router(erp_router)
api_router.include_router(master_router)
api_router.include_router(inventory_router)
api_router.include_router(purchasing_router)
api_router.include_router(reports_router)
