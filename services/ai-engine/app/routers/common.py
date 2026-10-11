"""Query-parameter handling shared by the HTTP routers."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import HTTPException

DEFAULT_WINDOW_HOURS = 8


def resolve_window(start: datetime | None, end: datetime | None) -> tuple[datetime, datetime]:
    """Default an omitted window to the last `DEFAULT_WINDOW_HOURS`."""
    resolved_end = end or datetime.now().replace(second=0, microsecond=0)
    resolved_start = start or resolved_end - timedelta(hours=DEFAULT_WINDOW_HOURS)
    if resolved_end <= resolved_start:
        raise HTTPException(status_code=400, detail="`to` must be later than `from`")
    return resolved_start, resolved_end