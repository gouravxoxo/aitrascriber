import re
from datetime import datetime
from typing import Optional


def parse_filename(filename: str) -> dict:
    """
    Parse metadata from VoiceIQ call recording filenames.
    Pattern: YYYY-MM-DD-HH-MM-SS_{UUID}_RGB{hex}...

    Returns dict with available metadata.
    """
    result: dict = {
        "call_date": None,
        "session_uuid": None,
        "publisher_hint": None,
    }

    # Strip extension
    name = filename.rsplit(".", 1)[0]
    parts = name.split("_", 2)

    # Date: 2026-01-13-22-19-44
    if len(parts) >= 1:
        date_str = parts[0]
        # Convert: 2026-01-13-22-19-44 → 2026-01-13 22:19:44
        date_normalized = re.sub(
            r"^(\d{4}-\d{2}-\d{2})-(\d{2})-(\d{2})-(\d{2})$",
            r"\1 \2:\3:\4",
            date_str
        )
        try:
            result["call_date"] = datetime.strptime(date_normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            pass

    # UUID
    if len(parts) >= 2:
        uuid_part = parts[1]
        if re.match(r"^[0-9a-f\-]{36}$", uuid_part, re.IGNORECASE):
            result["session_uuid"] = uuid_part

    # RGB-encoded publisher ID hint
    if len(parts) >= 3:
        data_part = parts[2]
        rgb_matches = re.findall(r"RGB([A-F0-9]+)", data_part, re.IGNORECASE)
        if rgb_matches:
            # First RGB block tends to be the publisher ID
            result["publisher_hint"] = rgb_matches[0]

    return result
