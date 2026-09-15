from datetime import datetime, timedelta

from radiotomate.enums import ScheduleMode
from radiotomate.models import Cart


def schedule_in_two_seconds(cart: Cart):
    upcoming = datetime.now() + timedelta(seconds=2)
    cart.schedule_mode = ScheduleMode.TIMED
    cart.schedule_year = str(upcoming.year)
    cart.schedule_month = str(upcoming.month)
    cart.schedule_day = str(upcoming.day)
    cart.schedule_hour = str(upcoming.hour)
    cart.schedule_minute = str(upcoming.minute)
    cart.schedule_second = str(upcoming.second)
