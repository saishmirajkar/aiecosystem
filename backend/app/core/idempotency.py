"""Idempotency for transaction-producing endpoints.

The idempotency record is inserted in the *same* DB transaction as the business posting.
A concurrent duplicate blocks on the unique index until the first transaction finishes, then
replays the stored response. If the posting fails, the transaction (and record) roll back, so
the client may safely retry with the same key.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.common import jsonable
from app.core.deps import Actor
from app.core.errors import AppError, Conflict
from app.models import IdempotencyRecord


class IdempotencyKeyRequired(AppError):
    status_code, code = 400, "idempotency_key_required"


def request_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(jsonable(payload), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def get_key(request: Request) -> str:
    key = (request.headers.get("idempotency-key") or "").strip()
    if not key or len(key) > 100:
        raise IdempotencyKeyRequired("Header 'Idempotency-Key' (1-100 chars) is required for this operation")
    return key


def run_idempotent(db: Session, actor: Actor, request: Request, payload: Any,
                   fn: Callable[[], Any], status_code: int = 201) -> JSONResponse:
    key = get_key(request)
    endpoint = f"{request.method} {request.url.path}"
    digest = request_hash(payload)
    record = IdempotencyRecord(key=key, user_id=actor.user_id, endpoint=endpoint, request_hash=digest)
    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        existing = db.execute(
            select(IdempotencyRecord).where(
                IdempotencyRecord.user_id == actor.user_id,
                IdempotencyRecord.endpoint == endpoint,
                IdempotencyRecord.key == key,
            )
        ).scalar_one()
        if existing.request_hash != digest:
            raise Conflict("Idempotency-Key was already used with a different request body", code="idempotency_key_reused")
        db.rollback()
        return JSONResponse(status_code=existing.response_status or 200, content=existing.response_body,
                            headers={"Idempotent-Replay": "true"})

    result = fn()
    if isinstance(result, tuple):  # (body, status): an outcome whose status is decided by the business logic
        result, status_code = result
    body = jsonable(result.model_dump(mode="json") if hasattr(result, "model_dump") else result)
    record.response_status = status_code
    record.response_body = body
    db.commit()
    return JSONResponse(status_code=status_code, content=body)
