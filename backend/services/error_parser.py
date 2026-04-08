import re
from typing import Optional


def _extract_status_code(message: str) -> Optional[int]:
    match = re.search(r"Status\s+(\d{3})", message)
    if match:
        return int(match.group(1))
    match = re.search(r"HTTP\s+(\d{3})", message)
    if match:
        return int(match.group(1))
    return None


def classify_error(message: Optional[str]) -> dict:
    text = (message or "").strip()
    if not text:
        return {
            "error_code": "unknown_error",
            "error_status": None,
            "error_stage": None,
            "error_summary": "Unknown processing error",
            "error_detail": "",
        }

    lowered = text.lower()
    status = _extract_status_code(text)

    if "split" in lowered or "ffmpeg" in lowered or "ffprobe" in lowered:
        code = "audio_processing_error"
        stage = "split"
        summary = "Audio processing failed while preparing the recording."
    elif "role" in lowered and "agent" in lowered:
        code = "role_detection_error"
        stage = "role_detect"
        summary = "Speaker role detection failed after transcription."
    elif "attribute 'audio'" in lowered or "purpose" in lowered:
        code = "sdk_error"
        stage = "transcribe"
        summary = "Mistral client mismatch or unsupported SDK request format."
    elif status == 429 or "capacity exceeded" in lowered:
        code = "upstream_capacity_exceeded"
        stage = "transcribe"
        summary = "Transcription provider capacity exceeded. Retry later."
    elif status == 503 and "overflow" in lowered:
        code = "upstream_overflow"
        stage = "transcribe"
        summary = "Transcription provider overflowed before handling the request."
    elif status == 504:
        code = "upstream_timeout"
        stage = "transcribe"
        summary = "Transcription provider timed out while processing the request."
    elif status in (502, 520):
        code = "upstream_gateway_error"
        stage = "transcribe"
        summary = f"Transcription provider returned upstream gateway error ({status})."
    elif status:
        code = f"upstream_http_{status}"
        stage = "transcribe"
        summary = f"Transcription provider returned HTTP {status}."
    elif "mistral_api_key is missing" in lowered:
        code = "missing_api_key"
        stage = "transcribe"
        summary = "Mistral API key is missing."
    else:
        code = "processing_error"
        stage = None
        summary = "Processing failed unexpectedly."

    return {
        "error_code": code,
        "error_status": status,
        "error_stage": stage,
        "error_summary": summary,
        "error_detail": text,
    }
