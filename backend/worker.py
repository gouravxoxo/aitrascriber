import asyncio
import traceback
from datetime import datetime

from database import AsyncSessionLocal, Job, Call, Segment
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from services.audio_processor import split_stereo_channels
from services.transcriber import transcribe_channel
from services.role_detector import detect_agent_channel
from services.merger import merge_channels

# Global asyncio queue — holds {job_id, call_id} dicts
job_queue: asyncio.Queue = asyncio.Queue()

# Max concurrent jobs (controls Voxtral API rate)
MAX_CONCURRENT = 3
semaphore = asyncio.Semaphore(MAX_CONCURRENT)


async def update_job(job_id: str, status: str, progress: int, msg: str):
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Job).where(Job.id == job_id))
        job = res.scalar_one_or_none()
        if job:
            job.status   = status
            job.progress = progress
            job.step_msg = msg
            if status in ("done", "error"):
                job.finished_at = datetime.utcnow()
            await db.commit()


async def update_call_status(call_id: str, status: str, **kwargs):
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(Call).where(Call.id == call_id))
        call = res.scalar_one_or_none()
        if call:
            call.status = status
            for k, v in kwargs.items():
                setattr(call, k, v)
            await db.commit()


async def save_segments(call_id: str, merged: list[dict]):
    async with AsyncSessionLocal() as db:
        for seg in merged:
            s = Segment(
                call_id=call_id,
                channel=seg["channel"],
                role=seg["role"],
                start_sec=seg["start_sec"],
                end_sec=seg["end_sec"],
                text=seg["text"],
                word_count=seg.get("word_count", len(seg["text"].split())),
                seq=seg["seq"],
            )
            db.add(s)
        await db.commit()


async def process_job(job_id: str, call_id: str):
    async with semaphore:
        try:
            # ── 1. Load call ──────────────────────────────────────────
            async with AsyncSessionLocal() as db:
                res = await db.execute(select(Call).where(Call.id == call_id))
                call = res.scalar_one_or_none()
                if not call:
                    raise ValueError(f"Call {call_id} not found")
                audio_path = call.audio_path

            await update_call_status(call_id, "processing")

            # ── 2. Split stereo → two mono WAV channels ───────────────
            await update_job(job_id, "splitting", 10, "Splitting audio channels...")
            ch0_path, ch1_path = await split_stereo_channels(audio_path, call_id)
            await update_call_status(call_id, "processing", ch0_path=ch0_path, ch1_path=ch1_path)

            # ── 3. Transcribe both channels in parallel ───────────────
            await update_job(job_id, "transcribing_agent", 25, "Transcribing channel 0...")

            # Run both transcriptions concurrently
            ch0_task = asyncio.create_task(transcribe_channel(ch0_path))
            ch1_task = asyncio.create_task(transcribe_channel(ch1_path))

            # Update progress mid-way
            await asyncio.sleep(2)
            await update_job(job_id, "transcribing_caller", 50, "Transcribing both channels...")

            ch0_segments, ch1_segments = await asyncio.gather(ch0_task, ch1_task)

            await update_job(job_id, "detecting_roles", 75, "Identifying agent and caller...")

            # ── 4. AI role detection ──────────────────────────────────
            agent_channel = await detect_agent_channel(ch0_segments, ch1_segments)
            await update_call_status(call_id, "processing", agent_channel=agent_channel)

            # ── 5. Merge channels by timestamp ────────────────────────
            await update_job(job_id, "merging", 88, "Merging transcript...")
            merged = merge_channels(ch0_segments, ch1_segments, agent_channel)

            # ── 6. Save segments ──────────────────────────────────────
            await save_segments(call_id, merged)
            await update_call_status(call_id, "done")
            await update_job(job_id, "done", 100, f"Done — {len(merged)} turns transcribed")

        except Exception as e:
            err = traceback.format_exc()
            print(f"[worker] ERROR job={job_id}: {err}")
            await update_job(job_id, "error", 0, f"Error: {str(e)[:200]}")
            await update_call_status(call_id, "error", error_msg=str(e)[:500])


async def start_worker():
    """
    Background worker loop. Pulls jobs from queue and processes them.
    Uses semaphore to limit concurrency (MAX_CONCURRENT parallel jobs).
    """
    print(f"[worker] Started — max concurrent jobs: {MAX_CONCURRENT}")
    while True:
        try:
            item = await job_queue.get()
            job_id  = item["job_id"]
            call_id = item["call_id"]
            print(f"[worker] Processing job={job_id} call={call_id}")
            # Fire and forget — semaphore limits concurrency
            asyncio.create_task(process_job(job_id, call_id))
            job_queue.task_done()
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"[worker] Queue error: {e}")
