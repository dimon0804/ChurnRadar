from datetime import datetime, timezone
from typing import Optional


def iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
