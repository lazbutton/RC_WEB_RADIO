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
    JINGLES = "jingles"
    TIMED = "timed"

    @property
    def label(self):
        """
        TODO this should be replaced by translation functions
        """
        match self:
            case ScheduleMode.JINGLES:
                return "Jingles"
            case ScheduleMode.TIMED:
                return "Timed"


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
