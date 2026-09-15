"""
Templates filter functions.

All must be reffered in `RADIOTOMATE_HELPERS` at the end of this file.
"""

from datetime import datetime, timedelta
from urllib.parse import urlparse

from markupsafe import Markup
from quart import request
from quart.templating import render_template_string
from quart_auth import current_user

from radiotomate.enums import CartMode, ScheduleMode


async def render_macro(path, macro, **kwargs) -> str:
    """
    Shortcut to render a macro. Parameters are
     - path to the file defining the macro
     - macro name
     - macro variables as keyword arguments

    As the path to the macro file is usually always the same in each module, we
    advise to create in each module a wrapper that hardcodes ``path``, so calls
    will be shorter.
    """
    return await render_template_string(
        f"""
        {{% from '{path}' import {macro} %}}
        {{{{ {macro}({', '.join(kwargs.keys())}) }}}}
        """,
        **kwargs,
    )


def in_blueprint(blueprint_name) -> bool:
    return request and request.blueprint and request.blueprint == blueprint_name


def date_span(dt: datetime):
    """
    Returns a formatted date in a span, and the complete datetime in a tooltip
    """
    return Markup(f"""<span title="{ dt.isoformat() }">
            { dt.date().strftime('%d/%m/%Y') }
        </span>""")


def duration_span(seconds: int) -> str:
    """
    This generates a span that will show a formatted duration
    """
    delta = timedelta(seconds=seconds)
    return str(delta)


def icon(raw_unicode: str) -> str:
    """
    Wraps an unicode character in a clickable icon element hidden to screen readers.
    """
    return Markup(
        f"""<span class="icon is-clickable" aria-hidden="true">{raw_unicode}</span>"""
    )


def hostname_span(url: str) -> Markup:
    parsed = urlparse(url)
    if parsed:
        displayed = parsed.hostname
    else:
        displayed = url
    return Markup(f"""<span title="{Markup.escape(url)}">{displayed}</span>""")


RADIOTOMATE_HELPERS = {
    "in_blueprint": in_blueprint,
    "date_span": date_span,
    "hostname_span": hostname_span,
    "icon": icon,
    "transitions_delay_ms": 250,
    "current_user": current_user,
    "duration_span": duration_span,
    "CartMode": CartMode,
    "ScheduleMode": ScheduleMode,
}
