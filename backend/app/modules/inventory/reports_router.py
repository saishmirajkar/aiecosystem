"""Report endpoints: list, run (JSON, paginated) and CSV export."""
from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response

from app.core.common import audit, jsonable
from app.core.database import utcnow
from app.core.deps import DB, Actor, require_permission
from app.modules.inventory.reports import REPORTS, Filters, run_report, to_csv

router = APIRouter(prefix="/reports", tags=["reports"])
VIEW = require_permission("reports.view")


@router.get("")
def list_reports(actor: Actor = Depends(VIEW)) -> list[dict]:
    return [{"id": r.id, "title": r.title, "description": r.description, "filters": r.filters,
             "columns": [{"key": k, "label": l} for k, l in r.columns]}
            for r in REPORTS.values() if all(actor.has(p) for p in r.requires)]


@router.get("/{report_id}")
def get_report(report_id: str, db: DB, actor: Actor = Depends(VIEW), date_from: date | None = None, date_to: date | None = None,
               product_id: uuid.UUID | None = None, warehouse_id: uuid.UUID | None = None, supplier_id: uuid.UUID | None = None,
               category_id: uuid.UUID | None = None, format: str = Query("json", pattern="^(json|csv)$"),
               page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000)):
    filters = Filters(date_from, date_to, product_id, warehouse_id, supplier_id, category_id)
    report, rows = run_report(db, actor, report_id, filters)
    if format == "csv":
        audit(db, actor, "report.export_csv", "report", report_id, {"filters": filters.__dict__, "rows": len(rows)})
        db.commit()
        filename = f"{report_id}_{utcnow().strftime('%Y%m%d_%H%M')}.csv"
        return Response(to_csv(report, rows), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    start = (page - 1) * page_size
    return jsonable({"id": report.id, "title": report.title, "columns": [{"key": k, "label": l} for k, l in report.columns],
                     "rows": rows[start:start + page_size], "total": len(rows), "page": page, "page_size": page_size,
                     "generated_at": utcnow()})
