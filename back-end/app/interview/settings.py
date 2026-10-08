"""Rollout switch separates persisted guidance from compatible public projection."""
import os


def guidance_responses_enabled() -> bool:
    value = os.getenv("INTERVIEW_GUIDANCE_RESPONSES", "off").lower()
    if value not in {"on", "off"}:
        raise ValueError("INTERVIEW_GUIDANCE_RESPONSES must be on or off")
    return value == "on"
