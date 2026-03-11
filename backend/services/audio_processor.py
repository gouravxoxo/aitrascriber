import asyncio
import os
import subprocess

CHANNEL_DIR = "storage/channels"
os.makedirs(CHANNEL_DIR, exist_ok=True)


async def probe_audio(path: str) -> dict:
    """Return basic audio metadata from ffprobe."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    import json
    if proc.returncode != 0 or not stdout:
        raise RuntimeError("ffprobe failed reading audio metadata")
    return json.loads(stdout)


async def get_audio_duration(path: str) -> float:
    """Get duration in seconds using ffprobe."""
    data = await probe_audio(path)
    return float(data["format"].get("duration", 0))


async def split_stereo_channels(audio_path: str, call_id: str) -> tuple[str, str]:
    """
    Split stereo MP3 into two mono WAV files at 16kHz.
    Returns (ch0_path, ch1_path) — ch0=left, ch1=right.
    Converts to 16kHz mono WAV for Voxtral compatibility.
    """
    ch0_path = os.path.join(CHANNEL_DIR, f"{call_id}_ch0.wav")
    ch1_path = os.path.join(CHANNEL_DIR, f"{call_id}_ch1.wav")
    probe = await probe_audio(audio_path)
    audio_stream = next((s for s in probe.get("streams", []) if s.get("codec_type") == "audio"), {})
    channels = int(audio_stream.get("channels") or 1)
    duration = float(probe.get("format", {}).get("duration") or 0)

    async def run_ffmpeg(*args: str):
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", *args,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg failed processing audio")

    async def extract_channel(src: str, dest: str, channel: int):
        await run_ffmpeg(
            "-i", src,
            "-af", f"pan=mono|c0=c{channel}",
            "-ar", "16000",
            "-ac", "1",
            dest,
        )

    async def extract_mono(src: str, dest: str):
        await run_ffmpeg(
            "-i", src,
            "-ar", "16000",
            "-ac", "1",
            dest,
        )

    async def create_silence(dest: str, seconds: float):
        silence_duration = max(seconds, 1.0)
        await run_ffmpeg(
            "-f", "lavfi",
            "-i", "anullsrc=r=16000:cl=mono",
            "-t", f"{silence_duration:.3f}",
            "-ar", "16000",
            "-ac", "1",
            dest,
        )

    if channels <= 1:
        await extract_mono(audio_path, ch0_path)
        await create_silence(ch1_path, duration)
        return ch0_path, ch1_path

    await asyncio.gather(
        extract_channel(audio_path, ch0_path, 0),
        extract_channel(audio_path, ch1_path, 1),
    )

    return ch0_path, ch1_path


async def get_channel_energy(wav_path: str) -> float:
    """Compute RMS energy of a mono WAV file (higher = more speech content)."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", wav_path,
        "-af", "volumedetect",
        "-f", "null", "-",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    output = stderr.decode()
    # Parse mean_volume: -27.3 dBFS
    for line in output.split("\n"):
        if "mean_volume" in line:
            try:
                db_val = float(line.split(":")[1].strip().replace(" dBFS", ""))
                return db_val  # higher (less negative) = more energy
            except Exception:
                pass
    return -99.0
