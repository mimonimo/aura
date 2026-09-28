"""Weekly drafts: Monday 09:00 Asia/Seoul, retry at most hourly on Monday."""
from datetime import datetime
from zoneinfo import ZoneInfo


def scheduled_week(now: datetime) -> str | None:
    local = now.astimezone(ZoneInfo("Asia/Seoul"))
    return local.date().isoformat() if local.weekday() == 0 and local.hour >= 9 else None


def run_schedule(stop, generate):
    last_attempt = None
    while not stop.is_set():
        now = datetime.now(ZoneInfo("Asia/Seoul"))
        week = scheduled_week(now)
        attempt = (week, now.hour)
        if week and attempt != last_attempt:
            last_attempt = attempt
            try:
                generate(week, now.date().isoformat())
            except Exception:
                import logging
                logging.getLogger(__name__).exception("Weekly report scheduling failed")
        stop.wait(60)
