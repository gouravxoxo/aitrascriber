def merge_channels(
    ch0_segments: list[dict],
    ch1_segments: list[dict],
    agent_channel: int = 0,
    gap_merge_sec: float = 1.2,
) -> list[dict]:
    """
    Merge two channel segment lists into one ordered conversation.

    Args:
        ch0_segments:   Voxtral segments from channel 0
        ch1_segments:   Voxtral segments from channel 1
        agent_channel:  Which channel (0 or 1) is the agent
        gap_merge_sec:  Merge consecutive same-speaker segments within this gap

    Returns:
        List of merged segments sorted by time, each with 'role' key.
    """
    all_segs = []

    for seg in ch0_segments:
        all_segs.append({
            **seg,
            "channel": 0,
            "role": "agent" if agent_channel == 0 else "caller",
        })

    for seg in ch1_segments:
        all_segs.append({
            **seg,
            "channel": 1,
            "role": "caller" if agent_channel == 0 else "agent",
        })

    # Sort by start time
    all_segs.sort(key=lambda x: x["start_sec"])

    # Collapse consecutive same-role segments within gap_merge_sec
    merged: list[dict] = []
    for seg in all_segs:
        if (
            merged
            and merged[-1]["role"] == seg["role"]
            and seg["start_sec"] - merged[-1]["end_sec"] <= gap_merge_sec
        ):
            # Extend previous segment
            merged[-1]["text"] += " " + seg["text"]
            merged[-1]["end_sec"] = seg["end_sec"]
        else:
            merged.append(dict(seg))

    # Add sequence index and word count
    for i, seg in enumerate(merged):
        seg["seq"]        = i
        seg["word_count"] = len(seg["text"].split())

    return merged
