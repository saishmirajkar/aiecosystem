"""Secure file upload handling: allow-list, size limit, magic-byte sniffing, random storage names."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.core.common import audit
from app.core.config import get_settings
from app.core.deps import Actor
from app.core.errors import ValidationFailed
from app.models import Attachment

ALLOWED: dict[str, tuple[str, ...]] = {
    ".png": ("image/png",), ".jpg": ("image/jpeg",), ".jpeg": ("image/jpeg",), ".webp": ("image/webp",),
    ".pdf": ("application/pdf",), ".csv": ("text/csv", "application/vnd.ms-excel", "text/plain"),
}
MAGIC: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",), ".pdf": (b"%PDF-",),
}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def storage_dir() -> Path:
    path = Path(get_settings().upload_dir).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "file")
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip() or "file"
    return base[:200]


def save_upload(db: Session, actor: Actor, upload: UploadFile, *, entity_type: str | None = None,
                entity_id: str | None = None, images_only: bool = False) -> Attachment:
    filename = _safe_filename(upload.filename or "")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED or (images_only and ext not in IMAGE_EXTS):
        raise ValidationFailed("File type not allowed", details={"allowed": sorted(IMAGE_EXTS if images_only else ALLOWED)})
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED[ext]:
        raise ValidationFailed("Content type does not match file extension")
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = upload.file.read(limit + 1)
    if len(data) > limit:
        raise ValidationFailed(f"File exceeds {get_settings().max_upload_mb} MB limit")
    if not data:
        raise ValidationFailed("File is empty")
    if ext in MAGIC and not any(data.startswith(m) for m in MAGIC[ext]):
        raise ValidationFailed("File content does not match its type")
    if ext == ".webp" and data[8:12] != b"WEBP":
        raise ValidationFailed("File content does not match its type")
    storage_name = secrets.token_hex(16) + ext
    (storage_dir() / storage_name).write_bytes(data)
    att = Attachment(filename=filename, content_type=ALLOWED[ext][0], size_bytes=len(data), storage_name=storage_name,
                     sha256=hashlib.sha256(data).hexdigest(), entity_type=entity_type, entity_id=entity_id,
                     uploaded_by=actor.user_id)
    db.add(att)
    db.flush()
    audit(db, actor, "attachment.upload", "attachment", att.id, {"filename": filename, "entity_type": entity_type, "entity_id": entity_id})
    return att
