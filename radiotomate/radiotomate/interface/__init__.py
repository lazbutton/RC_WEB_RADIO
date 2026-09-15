from collections.abc import Coroutine
from contextlib import ExitStack
from pathlib import Path
from time import time_ns

import quart
from multipart import (
    MultiDict,
    MultipartSegment,
    PushMultipartParser,
    parse_options_header,
)

_allowed_chars = ("-", ".", "_")


def safe_path(basedir: Path, filename: str, prefix: str = "") -> Path:
    """
    Generates a complete path in `basedir` from `filename`,
    and ensures nothing already exists there.
    Keeps only alphanumeric characters and the characters in _allowed_chars,
    also prefix the files with `prefix`, or 4 random digits to avoid collisions.
    """
    if not basedir:
        raise ValueError("Missing basedir")
    safe_filename = "".join(c for c in filename if c.isalnum() or c in _allowed_chars)
    while True:
        target_path = basedir / (prefix + safe_filename)
        if not target_path.exists():
            return target_path
        prefix = str(time_ns())[-4:]


def safe_int(s: str | None, default: int | None, positive=False) -> int | None:
    """
    Parses given string (extracted from a form dict) to an integer. Returns `default`
    if the value cannot be parsed to an integer. When `positive=True`, returned
    `default` if parsed value is negative.
    """
    if s is None:
        return default
    try:
        parsed = int(s)
        if positive and parsed < 0:
            return default
        else:
            return parsed
    except ValueError:
        return default


async def stream_form(
    base_path: Path | None = None,
) -> Coroutine[None, None, MultiDict]:
    header = quart.request.headers.get("Content-Type")
    content_type, options = parse_options_header(header)
    boundary = options["boundary"]
    form_fields = MultiDict()
    current_form_field = None
    current_file = None
    with ExitStack() as stack:
        parser = PushMultipartParser(boundary, strict=True)
        stack.enter_context(parser)

        async for chunk in quart.request.body:
            for result in parser.parse(chunk):
                if isinstance(result, MultipartSegment):
                    if result.filename:
                        target_path = safe_path(base_path, result.filename)
                        current_file = target_path.open("wb")
                        stack.enter_context(current_file)
                        form_fields[result.name] = {
                            "filename": result.filename,
                            "content_type": result.content_type,
                            "uploaded_to": target_path,
                        }
                    else:
                        current_form_field = result.name
                elif result:  # Result is a non-empty bytearray
                    if current_form_field:
                        form_fields[current_form_field] = result.decode()
                    else:
                        current_file.write(result)
                else:  # end of segment
                    current_form_field = None
                    current_file = None
            if parser.closed:
                break
    return form_fields
