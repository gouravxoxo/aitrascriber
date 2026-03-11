import os
import json
from mistralai import Mistral

MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")

SYSTEM_PROMPT = """You analyze phone call transcripts to identify which speaker is the AGENT 
(call center employee) and which is the CALLER (customer).

Rules:
- The AGENT typically speaks first with a greeting ("Thank you for calling...", "This is [name]...")
- The AGENT asks structured questions and offers services
- The CALLER describes a problem, need, or request they called about
- The CALLER responds to questions the agent asks

You will be given the first few transcript segments from channel 0 and channel 1.
Return ONLY valid JSON with no explanation: {"agent_channel": 0} or {"agent_channel": 1}"""


async def detect_agent_channel(
    ch0_segments: list[dict],
    ch1_segments: list[dict],
) -> int:
    """
    Use Mistral LLM to determine which channel (0 or 1) is the agent.
    Returns 0 or 1.
    Falls back to channel with more speaking time if AI fails.
    """
    client = Mistral(api_key=MISTRAL_API_KEY)

    # Take first 6 segments from each channel for context
    def fmt(segs, limit=6):
        lines = []
        for s in segs[:limit]:
            if s["text"].strip():
                lines.append(f"  [{s['start_sec']:.1f}s] {s['text']}")
        return "\n".join(lines) if lines else "  (silence)"

    prompt = f"""Channel 0 (first lines):
{fmt(ch0_segments)}

Channel 1 (first lines):
{fmt(ch1_segments)}

Which channel number is the AGENT?"""

    try:
        response = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=50,
        )
        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        agent_ch = int(data.get("agent_channel", 0))
        if agent_ch in (0, 1):
            return agent_ch
    except Exception as e:
        print(f"[role_detector] AI detection failed: {e}, using fallback")

    # Fallback: whoever has content in first 10 seconds is agent
    # (agent greets first)
    ch0_early = [s for s in ch0_segments if s["start_sec"] < 10 and s["text"].strip()]
    ch1_early = [s for s in ch1_segments if s["start_sec"] < 10 and s["text"].strip()]

    if ch0_early and not ch1_early:
        return 0
    if ch1_early and not ch0_early:
        return 1

    # Last fallback: channel with more total text = agent
    ch0_words = sum(len(s["text"].split()) for s in ch0_segments)
    ch1_words = sum(len(s["text"].split()) for s in ch1_segments)
    return 0 if ch0_words >= ch1_words else 1
