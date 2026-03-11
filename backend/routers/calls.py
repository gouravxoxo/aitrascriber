from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import select, desc, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db, Call, Segment, User
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

class CallSummary(BaseModel):
    id: str
    filename: str
    publisher_id: Optional[str]
    caller_id: Optional[str]
    call_date: Optional[datetime]
    duration_sec: Optional[float]
    status: str
    segment_count: int
    created_at: datetime

class CallDetail(BaseModel):
    id: str
    filename: str
    publisher_id: Optional[str]
    caller_id: Optional[str]
    call_date: Optional[datetime]
    duration_sec: Optional[float]
    status: str
    agent_channel: Optional[int]
    error_msg: Optional[str]
    created_at: datetime
    segments: List[SegmentOut]
    # stats
    agent_word_count: int
    caller_word_count: int
    total_turns: int

class CallsPage(BaseModel):
    items: List[CallSummary]
    total: int
    limit: int
    offset: int


# ─── HELPERS ──────────────────────────────────────────────────────────────────

def format_time(sec: float) -> str:
    m = int(sec // 60)
    s = sec % 60
    return f"{m:02d}:{s:05.2f}"

def build_txt_transcript(call: Call, segments: list) -> str:
    lines = []
    lines.append(f"VoiceIQ Transcript")
    lines.append(f"=" * 60)
    lines.append(f"File:         {call.filename}")
    if call.publisher_id:
        lines.append(f"Publisher ID: {call.publisher_id}")
    if call.caller_id:
        lines.append(f"Caller ID:    {call.caller_id}")
    if call.call_date:
        lines.append(f"Date:         {call.call_date.strftime('%Y-%m-%d %H:%M:%S')}")
    if call.duration_sec:
        lines.append(f"Duration:     {format_time(call.duration_sec)}")
    lines.append(f"=" * 60)
    lines.append("")

    prev_role = None
    for seg in segments:
        role_label = "Agent" if seg.role == "agent" else "Caller"
        ts = f"[{format_time(seg.start_sec)} → {format_time(seg.end_sec)}]"
        if seg.role != prev_role:
            lines.append("")
            lines.append(f"{role_label}  {ts}")
        else:
            lines.append(f"         {ts}")
        lines.append(f"  {seg.text}")
        prev_role = seg.role

    lines.append("")
    lines.append(f"— End of transcript —")
    return "\n".join(lines)


# ─── ROUTES ───────────────────────────────────────────────────────────────────

@router.get("", response_model=CallsPage)
async def list_calls(
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
    limit: int = 20,
    offset: int = 0,
    status: Optional[str]  = Query(None),
    search: Optional[str]  = Query(None),
    publisher_id: Optional[str] = Query(None),
):
    q = select(Call).where(Call.user_id == current_user.id)

    if status:
        q = q.where(Call.status == status)
    if publisher_id:
        q = q.where(Call.publisher_id == publisher_id)
    if search:
        q = q.where(or_(
            Call.filename.ilike(f"%{search}%"),
            Call.publisher_id.ilike(f"%{search}%"),
            Call.caller_id.ilike(f"%{search}%"),
        ))

    # Count
    count_q = select(func.count()).select_from(q.subquery())
    total_res = await db.execute(count_q)
    total = total_res.scalar() or 0

    # Page
    q = q.order_by(desc(Call.created_at)).limit(limit).offset(offset)
    result = await db.execute(q)
    calls = result.scalars().all()

    # Segment counts
    items = []
    for call in calls:
        seg_res = await db.execute(
            select(func.count(Segment.id)).where(Segment.call_id == call.id)
        )
        seg_count = seg_res.scalar() or 0
        items.append(CallSummary(
            id=call.id, filename=call.filename,
            publisher_id=call.publisher_id, caller_id=call.caller_id,
            call_date=call.call_date, duration_sec=call.duration_sec,
            status=call.status, segment_count=seg_count,
            created_at=call.created_at
        ))

    return CallsPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    result = await db.execute(
        select(Call)
        .where(Call.id == call_id, Call.user_id == current_user.id)
        .options(selectinload(Call.segments))
    )
    call = result.scalar_one_or_none()
    if not call:
        raise HTTPException(404, "Call not found")

    segs = sorted(call.segments, key=lambda s: s.seq or 0)
    agent_words  = sum(s.word_count or 0 for s in segs if s.role == "agent")
    caller_words = sum(s.word_count or 0 for s in segs if s.role == "caller")

    return CallDetail(
        id=call.id, filename=call.filename,
        publisher_id=call.publisher_id, caller_id=call.caller_id,
        call_date=call.call_date, duration_sec=call.duration_sec,
        status=call.status, agent_channel=call.agent_channel,
        error_msg=call.error_msg, created_at=call.created_at,
        segments=[SegmentOut(
            id=s.id, role=s.role, start_sec=s.start_sec,
            end_sec=s.end_sec, text=s.text, seq=s.seq or 0
        ) for s in segs],
        agent_word_count=agent_words,
        caller_word_count=caller_words,
        total_turns=len(segs),
    )


@router.get("/{call_id}/export/txt")
async def export_txt(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    result = await db.execute(
        select(Call)
        .where(Call.id == call_id, Call.user_id == current_user.id)
        .options(selectinload(Call.segments))
    )
    call = result.scalar_one_or_none()
    if not call:
        raise HTTPException(404, "Call not found")
    if call.status != "done":
        raise HTTPException(400, "Transcription not complete yet")

    segs = sorted(call.segments, key=lambda s: s.seq or 0)
    txt  = build_txt_transcript(call, segs)

    safe_name = call.filename.replace(" ", "_").split(".")[0]
    return PlainTextResponse(
        content=txt,
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}_transcript.txt"'
        }
    )


@router.delete("/{call_id}", status_code=204)
async def delete_call(
    call_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession   = Depends(get_db),
):
    import os
    result = await db.execute(
        select(Call).where(Call.id == call_id, Call.user_id == current_user.id)
    )
    call = result.scalar_one_or_none()
    if not call:
        raise HTTPException(404, "Call not found")

    # Delete audio files
    for path in [call.audio_path, call.ch0_path, call.ch1_path]:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    await db.delete(call)
    await db.commit()
