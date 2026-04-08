# 🎙️ VoiceIQ — Call Transcription Platform

Full-stack web app for transcribing call center recordings with agent/caller separation,
live progress streaming, and TXT download.

## Architecture

```
Browser → Nginx (port 3000)
              ↓ /api/* proxied
          FastAPI (port 8000)
              ↓
          Voxtral API (Mistral)
          SQLite database
          ffmpeg (audio splitting)
```

## How It Works

1. **Upload** audio (single or bulk) with optional Publisher ID + Caller ID
2. **ffmpeg** splits stereo MP3 → two mono WAV files (left = one speaker, right = other)
3. **Voxtral** transcribes each channel independently (no diarization needed — channels pre-separated)
4. **Mistral LLM** identifies which channel is Agent vs Caller from first lines
5. **Merger** interleaves both transcripts by timestamp into `Agent: / Caller:` format
6. **Live SSE stream** shows progress + segments appearing in real-time in browser
7. **Download TXT** exports clean formatted transcript

## Quick Start

### 1. Prerequisites
- Docker + Docker Compose
- Mistral API key (get at console.mistral.ai)
- ffmpeg (handled by Docker)

### 2. Setup
```bash
git clone <repo> voiceiq && cd voiceiq

# Create .env file
cp .env.example .env
# Edit .env and set your MISTRAL_API_KEY
# Optional: add MISTRAL_API_KEYS=key1,key2,key3 for failover/rotation

# Start everything
docker-compose up -d

# Open browser
open http://localhost:3000
```

### 3. First run
1. Go to `http://localhost:3000`
2. Click **Register** → create your account
3. Upload a call recording (.mp3, .wav, .m4a, .flac)
4. Optionally fill Publisher ID and Caller ID (alphanumeric, both optional)
5. Click **Start Transcription**
6. Watch segments stream in live
7. Click the call → view full transcript + dual waveform
8. Click **Download TXT** to export

## Bulk Upload

1. Switch to **Bulk Upload** mode
2. Drop multiple files at once
3. Each file gets its own Publisher ID + Caller ID row
4. All jobs process concurrently (up to 3 at a time)

## Project Structure

```
voiceiq/
├── backend/
│   ├── main.py                # FastAPI app
│   ├── database.py            # SQLAlchemy models (User, Call, Segment, Job)
│   ├── worker.py              # Background job processor
│   ├── routers/
│   │   ├── auth.py            # Login, register, JWT
│   │   ├── upload.py          # Single + bulk file upload
│   │   ├── jobs.py            # Job status + SSE streaming
│   │   └── calls.py           # Transcript view + TXT export
│   └── services/
│       ├── audio_processor.py # ffmpeg stereo split
│       ├── transcriber.py     # Voxtral API calls
│       ├── role_detector.py   # AI agent/caller detection
│       ├── merger.py          # Timestamp-based merge
│       └── filename_parser.py # Parse date from filename
├── frontend/
│   └── templates/
│       ├── index.html         # Login / Register page
│       ├── dashboard.html     # Upload + jobs list
│       └── call.html          # Transcript viewer + waveform
├── docker-compose.yml
└── .env.example
```

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/register` | Create account |
| POST | `/api/auth/login` | Get JWT token |
| GET  | `/api/auth/me` | Current user |
| POST | `/api/upload/single` | Upload single file |
| POST | `/api/upload/bulk` | Upload multiple files |
| GET  | `/api/jobs` | List all jobs |
| GET  | `/api/jobs/{id}/stream` | SSE progress stream |
| GET  | `/api/calls` | List transcribed calls |
| GET  | `/api/calls/{id}` | Full transcript + segments |
| GET  | `/api/calls/{id}/export/txt` | Download as TXT |
| DELETE | `/api/calls/{id}` | Delete call |

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `MISTRAL_API_KEY` | Primary Mistral API key | required if `MISTRAL_API_KEYS` is not set |
| `MISTRAL_API_KEYS` | Comma-separated Mistral API keys for rotation/failover | optional |
| `SECRET_KEY` | JWT signing secret | change in production |
| `DATABASE_URL` | SQLAlchemy DB URL | SQLite (./data/voiceiq.db) |

## Scaling Beyond 500 Calls/Day

For higher volume:
1. Switch SQLite → PostgreSQL: change `DATABASE_URL` in `.env`
2. Add Redis + Celery for distributed job queue (replace `worker.py`)
3. Add more backend workers: change `--workers 2` → `--workers 4` in Dockerfile

## Supported Audio Formats
- MP3, WAV, M4A, FLAC, OGG
- Stereo recordings (call center format) — channels automatically separated
- Mono recordings — Voxtral diarization used as fallback
- Max file size: 500MB (configurable)
- Max duration: 3 hours (Voxtral limit)
