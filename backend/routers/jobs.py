import json
import asyncio
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db, Job, Call, Segment, User
from routers.auth import get_current_user

router = APIRouter()


# ─── SCHEMAS ──────────────────────────────────────────────────────────────────

class SegmentOut(BaseModel):
    id: int
    role: str
    start_sec: float
    end_sec: float
    text: str
    seq: int

class JobOut(BaseModel):
    id: str
    call_id: str
    status: str
    progress: int
    step_msg: str
    created_at: datetime
    finished_at: Optional[datetime]
    filename: Optional[str]
    segments: List[SegmentOut] = []


# ─── ROUTES ───────────────────────────────────────────────────────────────────

@router.get("", response_model=List[JobOut])
async def list_jobs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
    limit: int = 50,
    offset: int = 0,
):
    result = await db.execute(
        select(Job)
        .join(Call, Job.call_id == Call.id)
        .where(Call.user_id == current_user.id)
        .options(selectinload(Job.call))
        .order_by(desc(Job.created_at))
        .limit(limit).offset(offset)
    )
    jobs = result.scalars().all()

    out = []
    for job in jobs:
        out.append(JobOut(
            id=job.id,
            call_id=job.call_id,
            status=job.status,
            progress=job.progress,
            step_msg=job.step_msg,
            created_at=job.created_at,
            finished_at=job.finished_at,
            filename=job.call.filename if job.call else None,
            segments=[],
        ))
    return out


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    result = await db.execute(
        select(Job)
        .join(Call, Job.call_id == Call.id)
        .where(Job.id == job_id, Call.user_id == current_user.id)
        .options(selectinload(Job.call).selectinload(Call.segments))
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Job not found")

    segments = []
    if job.call and job.call.segments:
        for seg in sorted(job.call.segments, key=lambda s: s.seq or 0):
            segments.append(SegmentOut(
                id=seg.id, role=seg.role, start_sec=seg.start_sec,
                end_sec=seg.end_sec, text=seg.text, seq=seg.seq or 0
            ))

    return JobOut(
        id=job.id, call_id=job.call_id, status=job.status,
        progress=job.progress, step_msg=job.step_msg,
        created_at=job.created_at, finished_at=job.finished_at,
        filename=job.call.filename if job.call else None,
        segments=segments,
    )


@router.get("/{job_id}/stream")
async def stream_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    """SSE endpoint — streams job progress + new segments as they appear."""

    # Verify ownership
    result = await db.execute(
        select(Job)
        .join(Call, Job.call_id == Call.id)
        .where(Job.id == job_id, Call.user_id == current_user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Job not found")

    async def event_generator():
        last_segment_id = 0
        while True:
            # Fresh DB session per poll
            from database import AsyncSessionLocal
            async with AsyncSessionLocal() as poll_db:
                job_res = await poll_db.execute(
                    select(Job).where(Job.id == job_id)
                    .options(selectinload(Job.call).selectinload(Call.segments))
                )
                job = job_res.scalar_one_or_none()
                if not job:
                    break

                # Get new segments since last poll
                new_segments = []
                if job.call and job.call.segments:
                    for seg in sorted(job.call.segments, key=lambda s: s.id):
                        if seg.id > last_segment_id:
                            new_segments.append({
                                "id": seg.id, "role": seg.role,
                                "start_sec": seg.start_sec, "end_sec": seg.end_sec,
                                "text": seg.text, "seq": seg.seq or 0
                            })
                            last_segment_id = seg.id

                payload = json.dumps({
                    "status":       job.status,
                    "progress":     job.progress,
                    "message":      job.step_msg,
                    "new_segments": new_segments,
                })
                yield f"data: {payload}\n\n"

                if job.status in ("done", "error"):
                    yield "data: {\"done\": true}\n\n"
                    break

            await asyncio.sleep(0.8)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )
