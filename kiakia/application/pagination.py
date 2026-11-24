"""Opaque pagination cursors (docs/adr/0003).

A cursor remembers "where the last page ended": the sort time and id of its last item,
plus which list it belongs to. It's base64url-encoded JSON so clients treat it as an
opaque token: they pass it back unchanged and never build or parse it themselves.

This is not a security token (a client could decode it); it's just a bookmark.
Anything malformed, or a cursor from a different list or sort order, is InvalidCursor (-> 400).
"""

import base64
import binascii
import json
from datetime import datetime

from kiakia.domain.errors import InvalidCursor
from kiakia.domain.ports import Keyset


def encode_cursor(scope: str, key: Keyset) -> str:
    at, item_id = key
    raw = json.dumps({"s": scope, "t": at.isoformat(), "i": item_id}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str, scope: str) -> Keyset:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        if not isinstance(data, dict) or data.get("s") != scope:
            raise InvalidCursor("cursor belongs to a different list or sort order")
        at = datetime.fromisoformat(data["t"])
        item_id = data["i"]
        if at.tzinfo is None or type(item_id) is not int or item_id < 1:
            raise InvalidCursor("is not a valid cursor")
        return at, item_id
    except InvalidCursor:
        raise
    except (ValueError, KeyError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        raise InvalidCursor("is not a valid cursor") from exc
