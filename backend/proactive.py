"""When the AI may reach out first.

Two halves: the schedule (how often to reconsider, and how long she has to have
been quiet before it is worth considering) and `evaluate`, which answers whether
right now is appropriate.

Every hour here is a local one. Celery runs in UTC, so without the conversion
below a "9:00-23:00 daytime" window would land somewhere else entirely — and
the failure mode is getting poked at three in the morning.

What this does not decide is *what to say*. The gate opens; the AI itself
chooses whether the moment deserves words, and is free to stay quiet.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    """Read an int setting, falling back on anything unusable. These are tuning
    knobs, so a typo should degrade to the default rather than refuse to boot.
    """
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if value >= minimum else default


TIMEZONE_NAME = os.environ.get("PROACTIVE_TIMEZONE", "UTC")

# Daytime window, in local hours. `start > end` wraps past midnight (22 -> 6),
# and start == end means "always daytime".
DAY_START_HOUR = _int_env("PROACTIVE_DAY_START_HOUR", 9, minimum=0)
DAY_END_HOUR = _int_env("PROACTIVE_DAY_END_HOUR", 23, minimum=0)

# How often to reconsider. Checking every ten minutes through the night buys
# nothing — the thresholds below are hours wide there anyway.
DAY_CHECK_MINUTES = _int_env("PROACTIVE_DAY_CHECK_MINUTES", 10)
NIGHT_CHECK_MINUTES = _int_env("PROACTIVE_NIGHT_CHECK_MINUTES", 120)

# How long the human has to have been quiet before reaching out is on the table.
DAY_SILENCE_MINUTES = _int_env("PROACTIVE_DAY_SILENCE_MINUTES", 60)
NIGHT_SILENCE_MINUTES = _int_env("PROACTIVE_NIGHT_SILENCE_MINUTES", 120)


def local_timezone():
    """The zone the human actually lives in, or UTC if the name is unusable."""
    try:
        return ZoneInfo(TIMEZONE_NAME)
    except Exception:
        return timezone.utc


def local_hour(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(local_timezone()).hour


def is_daytime(now: datetime | None = None) -> bool:
    hour = local_hour(now)
    if DAY_START_HOUR == DAY_END_HOUR:
        return True
    if DAY_START_HOUR < DAY_END_HOUR:
        return DAY_START_HOUR <= hour < DAY_END_HOUR
    return hour >= DAY_START_HOUR or hour < DAY_END_HOUR


def check_interval_minutes(now: datetime | None = None) -> int:
    return DAY_CHECK_MINUTES if is_daytime(now) else NIGHT_CHECK_MINUTES


def silence_threshold_minutes(now: datetime | None = None) -> int:
    return DAY_SILENCE_MINUTES if is_daytime(now) else NIGHT_SILENCE_MINUTES


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


ENABLED = _bool_env("PROACTIVE_ENABLED", False)

# Every window collapses to seconds here so one turn of the whole machine takes
# under a minute. Without it each pass at the wording costs an hour of waiting,
# which in practice means the wording never gets tuned.
DEBUG_FAST = _bool_env("PROACTIVE_DEBUG_FAST", False)

# Never interrupt an exchange that is still going.
HARD_BLOCK_MINUTES = _int_env("PROACTIVE_HARD_BLOCK_MINUTES", 10)
# Minimum gap between one outreach and the next, shared by every source.
GLOBAL_COOLDOWN_MINUTES = _int_env("PROACTIVE_COOLDOWN_MINUTES", 60)
# Ceiling on the whole day, so pacing cannot add up to something relentless.
DAILY_CAP = _int_env("PROACTIVE_DAILY_CAP", 2)

# Whether the small hours count toward how long she has been quiet. Off by
# default: counting them means every morning opens having "not spoken for ten
# hours", and outreach degenerates into a daily alarm clock.
NIGHT_COUNTS_AS_SILENCE = _bool_env("PROACTIVE_NIGHT_COUNTS_AS_SILENCE", False)


class ProactiveSource(str, Enum):
    """Why the AI wants to speak up. Distinct kinds, one shared budget — adding
    another can never multiply how often the human is interrupted.
    """

    silence_checkin = "silence_checkin"
    evening = "evening"
    memory_recall = "memory_recall"
    weather = "weather"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str


def hard_block() -> timedelta:
    return timedelta(seconds=5) if DEBUG_FAST else timedelta(minutes=HARD_BLOCK_MINUTES)


def quiet_window(now: datetime | None = None) -> timedelta:
    if DEBUG_FAST:
        return timedelta(seconds=15)
    return timedelta(minutes=silence_threshold_minutes(now))


def global_cooldown() -> timedelta:
    return timedelta(seconds=20) if DEBUG_FAST else timedelta(minutes=GLOBAL_COOLDOWN_MINUTES)


def awake_minutes(since: datetime, now: datetime | None = None) -> float:
    """Daytime minutes between the two instants, sampled in quarter hours.

    Walking the span is the only honest way to do this once it crosses a
    night: the answer depends on how much of the gap fell inside the window,
    not on its length. The walk is capped so a very old timestamp — a fresh
    database, a long absence — cannot spin.
    """
    now = now or datetime.now(timezone.utc)
    if since >= now:
        return 0.0

    cursor = max(since, now - timedelta(days=14))
    step = timedelta(minutes=15)
    total = 0.0
    while cursor < now:
        nxt = min(cursor + step, now)
        if is_daytime(cursor + (nxt - cursor) / 2):
            total += (nxt - cursor).total_seconds() / 60
        cursor = nxt
    return total


def silence_minutes(since: datetime, now: datetime | None = None) -> float:
    """How long she has been quiet, in the units the thresholds are written in."""
    now = now or datetime.now(timezone.utc)
    if NIGHT_COUNTS_AS_SILENCE or DEBUG_FAST:
        return max(0.0, (now - since).total_seconds() / 60)
    return awake_minutes(since, now)


def evaluate(
    source: ProactiveSource,
    last_human_message_at: datetime | None,
    last_delivered_at: datetime | None,
    delivered_today: int,
    now: datetime | None = None,
) -> Decision:
    """Whether reaching out is appropriate right now.

    Silence is measured from her last message rather than from the last message
    of any kind. Conversations almost always end on the AI's reply, so treating
    "the AI spoke last" as disqualifying would be a permanent block — the
    resting state of every finished exchange. What actually needs preventing is
    following up on itself, and the cooldown below already does that.
    """
    now = now or datetime.now(timezone.utc)

    if not ENABLED:
        return Decision(False, "proactive outreach is disabled")

    if delivered_today >= DAILY_CAP and not DEBUG_FAST:
        return Decision(False, f"daily cap reached ({delivered_today}/{DAILY_CAP})")

    if last_delivered_at is not None:
        since_last = now - last_delivered_at
        if since_last < global_cooldown():
            left = int((global_cooldown() - since_last).total_seconds())
            return Decision(False, f"cooldown: {left}s left before any source may speak")

    if last_human_message_at is None:
        return Decision(False, "no message from her yet — nothing to be quiet after")

    since_her = now - last_human_message_at
    if since_her < hard_block():
        return Decision(False, f"she is mid-conversation ({int(since_her.total_seconds())}s ago)")

    quiet = quiet_window(now)
    quiet_for = timedelta(minutes=silence_minutes(last_human_message_at, now))
    if quiet_for < quiet:
        return Decision(
            False,
            f"quiet for {int(quiet_for.total_seconds() / 60)}min, needs {int(quiet.total_seconds() / 60)}min",
        )

    return Decision(True, f"{source.value}: quiet for {int(quiet_for.total_seconds() / 60)}min")
