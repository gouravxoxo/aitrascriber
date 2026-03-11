import os
import uuid
import shutil
import json
import re
from typing import List, Optional
from urllib.parse import urlparse, unquote

import httpx
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
ALLOWED_EXTENSIONS = (".mp3", ".wav", ".m4a", ".flac", ".ogg")
CONTENT_TYPE_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/vorbis": ".ogg",
}


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


class UrlUploadRequest(BaseModel):
    url: str
    publisher_id: Optional[str] = None
    caller_id: Optional[str] = None


class BulkUrlUploadItem(BaseModel):
    url: str
    publisher_id: Optional[str] = None
    caller_id: Optional[str] = None


class BulkUrlUploadRequest(BaseModel):
    items: List[BulkUrlUploadItem]


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

    # Save file to disk
    ext = get_extension(file.filename, file.content_type)
    dest_path = os.path.join(UPLOAD_DIR, f"{call_id}{ext}")
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    return await create_records_for_path(
        filename=file.filename,
        dest_path=dest_path,
        call_id=call_id,
        publisher_id=publisher_id,
        caller_id=caller_id,
        user=user,
        db=db,
    )


def get_extension(filename: Optional[str], content_type: Optional[str] = None) -> str:
    ext = os.path.splitext((filename or "").strip())[1].lower()
    if ext in ALLOWED_EXTENSIONS:
        return ext

    normalized_type = (content_type or "").split(";")[0].strip().lower()
    guessed = CONTENT_TYPE_EXTENSIONS.get(normalized_type)
    if guessed:
        return guessed

    raise HTTPException(400, "Unsupported file format")


def extract_filename_from_headers(content_disposition: Optional[str]) -> Optional[str]:
    if not content_disposition:
        return None

    utf_match = re.search(r"filename\*\s*=\s*UTF-8''([^;]+)", content_disposition, re.IGNORECASE)
    if utf_match:
        return unquote(utf_match.group(1))

    basic_match = re.search(r'filename\s*=\s*"?(?P<name>[^";]+)"?', content_disposition, re.IGNORECASE)
    if basic_match:
        return basic_match.group("name")
    return None


def filename_from_url(source_url: str, content_disposition: Optional[str], content_type: Optional[str]) -> str:
    header_name = extract_filename_from_headers(content_disposition)
    if header_name:
        base = os.path.basename(header_name)
    else:
        parsed = urlparse(source_url)
        base = os.path.basename(unquote(parsed.path))

    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
    if not safe_name:
        safe_name = "recording"

    ext = get_extension(safe_name, content_type)
    if not safe_name.lower().endswith(ext):
        safe_name = f"{safe_name}{ext}"
    return safe_name


async def download_url_to_storage(source_url: str, call_id: str) -> tuple[str, str]:
    parsed = urlparse(source_url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, "Recording URL must start with http:// or https://")

    timeout = httpx.Timeout(connect=20.0, read=600.0, write=60.0, pool=60.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            async with client.stream("GET", source_url) as response:
                if response.status_code >= 400:
                    raise HTTPException(400, f"Failed to download recording: HTTP {response.status_code}")

                filename = filename_from_url(
                    source_url,
                    response.headers.get("content-disposition"),
                    response.headers.get("content-type"),
                )
                dest_path = os.path.join(UPLOAD_DIR, f"{call_id}{get_extension(filename, response.headers.get('content-type'))}")
                with open(dest_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
        except httpx.InvalidURL as exc:
            raise HTTPException(400, f"Invalid recording URL: {exc}") from exc
        except httpx.HTTPError as exc:
            raise HTTPException(400, f"Failed to download recording: {exc}") from exc

    return filename, dest_path


async def create_records_for_path(
    filename: str,
    dest_path: str,
    call_id: str,
    publisher_id: Optional[str],
    caller_id: Optional[str],
    user: User,
    db: AsyncSession
) -> dict:
    job_id  = str(uuid.uuid4())

    # Parse filename metadata
    meta = parse_filename(filename)

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
        filename=filename,
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
        "filename": filename,
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
    get_extension(file.filename, file.content_type)

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
        try:
            get_extension(file.filename, file.content_type)
        except HTTPException:
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


@router.post("/url", response_model=UploadResult)
async def upload_single_url(
    payload: UrlUploadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    call_id = str(uuid.uuid4())
    filename, dest_path = await download_url_to_storage(payload.url, call_id)
    return await create_records_for_path(
        filename=filename,
        dest_path=dest_path,
        call_id=call_id,
        publisher_id=payload.publisher_id,
        caller_id=payload.caller_id,
        user=current_user,
        db=db,
    )


@router.post("/url/bulk", response_model=List[UploadResult])
async def upload_bulk_urls(
    payload: BulkUrlUploadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not payload.items:
        raise HTTPException(400, "No recording URLs provided")

    results = []
    for item in payload.items:
        call_id = str(uuid.uuid4())
        filename, dest_path = await download_url_to_storage(item.url, call_id)
        result = await create_records_for_path(
            filename=filename,
            dest_path=dest_path,
            call_id=call_id,
            publisher_id=item.publisher_id,
            caller_id=item.caller_id,
            user=current_user,
            db=db,
        )
        results.append(result)

    return results
