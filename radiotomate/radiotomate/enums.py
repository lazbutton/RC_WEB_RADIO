"""
Radiotomate's enumerated types: because some fixed-list values do not need a DB table,
but we don't want to hardcode their string value either.

Those are in their own module to avoid dependency loops.
"""

from enum import Enum, IntEnum


class CartMode(Enum):
    """
    Cart.mode
    """

    PLAYLIST = "playlist"
    PLAYLIST_LOOP = "playlist_loop"
    RANDOM = "random"
    RELAY = "relay"

    @property
    def label(self):
        """
        TODO this should be replaced by translation functions
        """
        match self:
            case CartMode.PLAYLIST:
                return "Playlist"
            case CartMode.PLAYLIST_LOOP:
                return "Looping Playlist"
            case CartMode.RANDOM:
                return "Random"
            case CartMode.RELAY:
                return "Relay"


class ScheduleMode(Enum):
    """
    Who decides *when* a cart plays.

    ``CLOCK`` is the target (ENF-05): the cart is only aired by clock positions,
    anchors or the desk pads; no cron, no APScheduler job. ``TIMED`` keeps the
    legacy cron for carts the clocks do not cover yet.
    """

    CLOCK = "clock"
    JINGLES = "jingles"
    TIMED = "timed"

    @property
    def label(self):
        """
        TODO this should be replaced by translation functions
        """
        match self:
            case ScheduleMode.CLOCK:
                return "Horloge"
            case ScheduleMode.JINGLES:
                return "Jingles"
            case ScheduleMode.TIMED:
                return "Timed"

    @property
    def uses_cron(self) -> bool:
        return self is ScheduleMode.TIMED


class PositionKind(Enum):
    MUSIQUE = "musique"
    JINGLE = "jingle"
    SON = "son"
    PUB = "pub"


class WhenMode(Enum):
    SEQUENTIAL = "sequential"
    ANCHORED = "anchored"


class SyncMode(Enum):
    DURE = "dure"
    MOLLE = "molle"


class RundownStatus(Enum):
    PLANNED = "planned"
    RESERVED = "reserved"
    PENDING_PUSH = "pending_push"
    SENT = "sent"
    ACCEPTED = "accepted"
    IN_QUEUE = "in_queue"
    ON_AIR = "on_air"
    PLAYED = "played"
    SKIPPED = "skipped"
    REPLACED = "replaced"
    RESCUE = "rescue"
    FAILED = "failed"


class CommandStatus(Enum):
    PENDING = "pending"
    SENDING = "sending"
    ACKNOWLEDGED = "acknowledged"
    FAILED = "failed"
    EXPIRED = "expired"


class PlayoutAction(Enum):
    QUEUE = "queue"
    SKIP = "skip"
    RELAY_START = "relay_start"
    RELAY_STOP = "relay_stop"


class DayNames(IntEnum):
    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6

    @property
    def display(self):
        """
        TODO this should be replaced by translation functions
        """
        return self.name.capitalize()

    def matches(self, other: int | str) -> bool:
        if isinstance(other, str):
            return str(self.value) == other
        else:
            return self.value == other
