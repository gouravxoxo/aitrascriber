import os
import uuid
import shutil
import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db, Call, Job, User
from routers.auth import get_current_user
from services.filename_parser import parse_filename
from services.audio_processor import get_audio_duration
from worker import job_queue

router = APIRouter()

UPLOAD_DIR = "storage/audio"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# ─── SCHEMAS ──────────────────────────────────────────────────────────────────

class UploadResult(BaseModel):
    call_id: str
    job_id: str
    filename: str
    duration_sec: Optional[float]
    publisher_id: Optional[str]
    caller_id: Optional[str]


class BulkUploadItem(BaseModel):
    filename: str
    publisher_id: Optional[str] = None
    caller_id: Optional[str] = None


# ─── HELPERS ──────────────────────────────────────────────────────────────────

async def save_file_and_create_records(
    file: UploadFile,
    publisher_id: Optional[str],
    caller_id: Optional[str],
    user: User,
    db: AsyncSession
) -> dict:
    """Save uploaded file, create Call + Job records, enqueue job."""

    call_id = str(uuid.uuid4())
    job_id  = str(uuid.uuid4())

    # Save file to disk
    ext = os.path.splitext(file.filename)[1] or ".mp3"
    dest_path = os.path.join(UPLOAD_DIR, f"{call_id}{ext}")
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    # Parse filename metadata
    meta = parse_filename(file.filename)

    # Get duration
    duration = None
    try:
        duration = await get_audio_duration(dest_path)
    except Exception:
        pass

    # Create Call record
    call = Call(
        id=call_id,
        user_id=user.id,
        filename=file.filename,
        publisher_id=publisher_id or None,
        caller_id=caller_id or None,
        call_date=meta.get("call_date"),
        duration_sec=duration,
        status="pending",
        audio_path=dest_path,
    )
    db.add(call)

    # Create Job record
    job = Job(
        id=job_id,
        call_id=call_id,
        status="queued",
        progress=0,
        step_msg="Queued for processing"
    )
    db.add(job)
    await db.commit()

    # Enqueue
    await job_queue.put({"job_id": job_id, "call_id": call_id})

    return {
        "call_id": call_id,
        "job_id": job_id,
        "filename": file.filename,
        "duration_sec": duration,
        "publisher_id": publisher_id or None,
        "caller_id": caller_id or None,
    }


# ─── ROUTES ───────────────────────────────────────────────────────────────────

@router.post("/single", response_model=UploadResult)
async def upload_single(
    file: UploadFile = File(...),
    publisher_id: Optional[str] = Form(None),
    caller_id: Optional[str]    = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(400, "No file provided")
    if not file.filename.lower().endswith((".mp3", ".wav", ".m4a", ".flac", ".ogg")):
        raise HTTPException(400, "Unsupported file format")

    result = await save_file_and_create_records(
        file, publisher_id, caller_id, current_user, db
    )
    return result


@router.post("/bulk", response_model=List[UploadResult])
async def upload_bulk(
    files: List[UploadFile] = File(...),
    # JSON string: [{"filename": "x.mp3", "publisher_id": "ABC", "caller_id": "123"}, ...]
    # If not provided, all files get the same shared metadata
    metadata_json: Optional[str] = Form(None),
    shared_publisher_id: Optional[str] = Form(None),
    shared_caller_id: Optional[str]    = Form(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    if not files:
        raise HTTPException(400, "No files provided")

    # Parse per-file metadata if provided
    per_file_meta: dict[str, dict] = {}
    if metadata_json:
        try:
            items = json.loads(metadata_json)
            for item in items:
                per_file_meta[item["filename"]] = {
                    "publisher_id": item.get("publisher_id") or None,
                    "caller_id":    item.get("caller_id") or None,
                }
        except Exception:
            raise HTTPException(400, "Invalid metadata_json format")

    results = []
    for file in files:
        if not file.filename.lower().endswith((".mp3", ".wav", ".m4a", ".flac", ".ogg")):
            continue

        # Per-file metadata takes priority, fallback to shared
        file_meta = per_file_meta.get(file.filename, {})
        pub_id = file_meta.get("publisher_id") or shared_publisher_id or None
        cal_id = file_meta.get("caller_id")    or shared_caller_id    or None

        result = await save_file_and_create_records(
            file, pub_id, cal_id, current_user, db
        )
        results.append(result)

    return results
