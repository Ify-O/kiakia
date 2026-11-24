"""Kiakia command line, and the COMPOSITION ROOT.

This is the only module that knows the concrete classes (TelegramSource,
PostgresStore). It builds them and hands them to the use cases, which only
know the ports. Swapping Telegram for Meta later means changing wiring here.

    python -m kiakia.cli import --limit 10   # start small
    python -m kiakia.cli import              # everything pending
    python -m kiakia.cli waiting             # customers still waiting for a reply
    python -m kiakia.cli stats               # what's in the database
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone

from kiakia.adapters.postgres.repository import PostgresStore, connect
from kiakia.adapters.telegram.client import TelegramClient
from kiakia.adapters.telegram.source import TelegramSource
from kiakia.application.import_messages import ImportMessages
from kiakia.application.waiting import ListWaitingCustomers
from kiakia.config import ConfigError, Settings, load_settings
from kiakia.domain.errors import KiakiaError

try:
    from zoneinfo import ZoneInfo
    LOCAL_TZ = ZoneInfo("Africa/Lagos")   # stored in UTC, shown in business time
except Exception:                          # pragma: no cover
    LOCAL_TZ = timezone.utc


def _store(settings: Settings) -> PostgresStore:
    return PostgresStore(connect(settings.db_host, settings.db_port, settings.db_user,
                                 settings.db_password, settings.db_name))


def cmd_import(args) -> None:
    settings = load_settings()
    store = _store(settings)
    source = TelegramSource(TelegramClient(settings.telegram_bot_token))
    use_case = ImportMessages(source=source, messages=store, cursors=store, runs=store)
    s = use_case.run(max_records=args.limit)
    print(f"Imported from {source.name}: {s.seen} updates in {s.batches} batch(es) -> "
          f"{s.saved} messages saved, {s.ignored} ignored, {s.bad} bad.")


def _ago(delta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min"
    return f"{hours // 24} days"


def cmd_waiting(args) -> None:
    store = _store(load_settings(require_telegram=False))
    waiting = ListWaitingCustomers(store).run()
    now = datetime.now(timezone.utc)
    if not waiting:
        print("Nobody is waiting. Every customer has a reply. 🎉")
        return
    print(f"{len(waiting)} customer(s) waiting for a reply (oldest first):\n")
    for w in waiting:
        local = w.waiting_since.astimezone(LOCAL_TZ)
        preview = (w.last_message_preview or "")[:50]
        print(f"  ⏳ {w.contact_name[:24]:24}  {w.channel.value:9}  waiting {_ago(w.waited(now)):>12}"
              f"  (since {local:%d %b %H:%M})  \"{preview}\"")


def cmd_stats(args) -> None:
    c = _store(load_settings(require_telegram=False)).counts()
    print(f"Contacts: {c['contacts']}   Conversations: {c['conversations']}   Messages: {c['messages']}")
    for kind, n in c["by_kind"].items():
        print(f"  {kind:9} {n}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="kiakia", description="Kiakia: every customer, answered fast.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logs")
    sub = parser.add_subparsers(dest="command", required=True)
    p_import = sub.add_parser("import", help="fetch new messages and store them")
    p_import.add_argument("--limit", type=int, default=None, help="stop after N updates (start small)")
    p_import.set_defaults(func=cmd_import)
    sub.add_parser("waiting", help="customers still waiting for a reply").set_defaults(func=cmd_waiting)
    sub.add_parser("stats", help="what's in the database").set_defaults(func=cmd_stats)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)  # its debug logs would show URLs (token!)
    try:
        args.func(args)
    except (KiakiaError, ConfigError) as exc:
        print(f"Error: {exc}", file=sys.stderr)   # clear message, no stack trace, no secrets
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
