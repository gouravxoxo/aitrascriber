import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from database import init_db
from routers import auth, upload, jobs, calls
from worker import start_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    worker_task = asyncio.create_task(start_worker())
    yield
    worker_task.cancel()

app = FastAPI(title="VoiceIQ API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router,   prefix="/api/auth",   tags=["auth"])
app.include_router(upload.router, prefix="/api/upload", tags=["upload"])
app.include_router(jobs.router,   prefix="/api/jobs",   tags=["jobs"])
app.include_router(calls.router,  prefix="/api/calls",  tags=["calls"])


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

# Serve uploaded audio files
os.makedirs("storage/audio", exist_ok=True)
app.mount("/audio", StaticFiles(directory="storage/audio"), name="audio")
