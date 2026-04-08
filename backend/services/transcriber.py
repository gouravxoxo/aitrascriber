import asyncio
import os
from typing import Any

import httpx
from mistralai import Mistral

MODEL = os.getenv("MISTRAL_TRANSCRIBE_MODEL", "voxtral-mini-latest")

from services.mistral_pool import (
    get_rotated_mistral_api_keys,
    is_retryable_mistral_error,
    key_label,
)


def _to_dict(value: Any) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    return {}


def _transcribe_sync(wav_path: str, api_key: str) -> list[dict]:
    client = Mistral(api_key=api_key)

    if hasattr(client, "audio") and hasattr(client.audio, "transcriptions"):
        with open(wav_path, "rb") as audio_file:
            result = client.audio.transcriptions.complete(
                model=MODEL,
                file={
                    "file_name": os.path.basename(wav_path),
                    "content": audio_file,
                },
                timestamp_granularities=["segment"],
            )
        data = _to_dict(result)
    else:
        with open(wav_path, "rb") as audio_file:
            response = httpx.post(
                "https://api.mistral.ai/v1/audio/transcriptions",
                headers={"Authorization": f"Bearer {api_key}"},
                data={
                    "model": MODEL,
                    "timestamp_granularities[]": "segment",
                },
                files={
                    "file": (os.path.basename(wav_path), audio_file, "audio/wav"),
                },
                timeout=600.0,
            )
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:500]}")
        data = response.json()

    segments = []
    for seg in data.get("segments") or []:
        seg_data = _to_dict(seg)
        text = (seg_data.get("text") or "").strip()
        if text:
            segments.append({
                "start_sec": float(seg_data.get("start", 0)),
                "end_sec": float(seg_data.get("end", 0)),
                "text": text,
            })

    text = (data.get("text") or "").strip()
    if not segments and text:
        segments.append({
            "start_sec": 0.0,
            "end_sec": 0.0,
            "text": text,
        })

    return segments


async def transcribe_channel(wav_path: str) -> list[dict]:
    api_keys = get_rotated_mistral_api_keys()
    if not api_keys:
        raise RuntimeError("MISTRAL_API_KEY is missing")

    errors: list[str] = []
    for index, api_key in enumerate(api_keys, start=1):
        try:
            segments = await asyncio.to_thread(_transcribe_sync, wav_path, api_key)
            print(
                f"[transcriber] {len(segments)} segments from {os.path.basename(wav_path)} "
                f"using key {index}/{len(api_keys)} ({key_label(api_key)})"
            )
            return segments
        except Exception as exc:
            msg = str(exc)
            errors.append(
                f"key {index}/{len(api_keys)} ({key_label(api_key)}): {msg}"
            )
            if not is_retryable_mistral_error(msg):
                break

    detail = " | ".join(errors[:6])
    raise RuntimeError(f"Transcription request failed after {len(errors)} key attempt(s): {detail}")
