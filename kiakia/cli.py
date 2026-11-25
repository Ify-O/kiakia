"""Kiakia command line: parses commands and prints results. Nothing else.

It never builds adapters itself: it asks kiakia.bootstrap (the composition root)
for ready-made use cases, so the future web API can reuse the same wiring.

    python -m kiakia.cli import --limit 10   # start small
    python -m kiakia.cli import              # everything pending
    python -m kiakia.cli watch               # keep importing live (Ctrl+C to stop)
    python -m kiakia.cli waiting             # customers still waiting for a reply
    python -m kiakia.cli stats               # what's in the database
"""

import argparse
import logging
import sys
import time
from datetime import UTC, datetime, timedelta

from kiakia.application.watch import WatchMessages
from kiakia.bootstrap import app
from kiakia.config import ConfigError, load_settings
from kiakia.demo_seed import seed
from kiakia.domain.errors import KiakiaError


def cmd_import(args: argparse.Namespace) -> None:
    with app(load_settings()) as services:
        s = services.import_messages().run(max_records=args.limit)
    print(
        f"Imported from {s.source}: {s.seen} updates in {s.batches} batch(es) -> "
        f"{s.saved} messages saved, {s.ignored} ignored, {s.bad} bad (kept in rejected_updates)."
    )


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours} h {minutes} min"
    return f"{hours // 24} days"


def cmd_watch(args: argparse.Namespace) -> None:
    """Keep importing new messages live (long polling). Stop with Ctrl+C."""
    settings = load_settings()
    print("👀 Watching Telegram for new messages (Ctrl+C to stop)…", flush=True)
    try:
        with app(settings) as services:
            watcher = WatchMessages(services.import_messages(wait_seconds=25), sleep=time.sleep)
            watcher.run(on_saved=lambda n: print(f"📩 {n} new message(s) saved", flush=True))
    except KeyboardInterrupt:
        print("\nStopped watching.")


def cmd_seed_demo(args: argparse.Namespace) -> None:
    """Reset the DEMO database and fill it with fake customers (ADR 0008)."""
    settings = load_settings(require_telegram=False)
    if not settings.demo_mode:
        raise ConfigError("seed-demo only runs in demo mode: set KIAKIA_DEMO=1 and POSTGRES_DB=kiakia_demo")
    with app(settings) as services:
        n = seed(services.store)
    print(f"🎬 Demo database '{settings.db_name}' filled with {n} fake customers.")


def cmd_waiting(args: argparse.Namespace) -> None:
    settings = load_settings(require_telegram=False)
    with app(settings) as services:
        waiting = services.list_waiting_customers().run()
    now = datetime.now(UTC)
    if not waiting:
        print("Nobody is waiting. Every customer has a reply. 🎉")
        return
    print(f"{len(waiting)} customer(s) waiting for a reply (oldest first):\n")
    for w in waiting:
        local = w.waiting_since.astimezone(settings.business_tz)  # stored in UTC, shown locally
        preview = (w.last_message_preview or "")[:50]
        print(
            f"  ⏳ {w.contact_name[:24]:24}  {w.channel.value:9}  waiting {_ago(w.waited(now)):>12}"
            f'  (since {local:%d %b %H:%M})  "{preview}"'
        )


def cmd_stats(args: argparse.Namespace) -> None:
    with app(load_settings(require_telegram=False)) as services:
        st = services.get_inbox_stats().run()
    print(f"Contacts: {st.contacts}   Conversations: {st.conversations}   Messages: {st.messages}")
    for kind, n in st.by_kind.items():
        print(f"  {kind.value:9} {n}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kiakia", description="Kiakia: every customer, answered fast.")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logs")
    sub = parser.add_subparsers(dest="command", required=True)
    p_import = sub.add_parser("import", help="fetch new messages and store them")
    p_import.add_argument("--limit", type=int, default=None, help="stop after N updates (start small)")
    p_import.set_defaults(func=cmd_import)
    sub.add_parser("watch", help="keep importing new messages live (Ctrl+C to stop)").set_defaults(
        func=cmd_watch
    )
    sub.add_parser("waiting", help="customers still waiting for a reply").set_defaults(func=cmd_waiting)
    sub.add_parser("seed-demo", help="demo mode only: reset + fill the demo database").set_defaults(
        func=cmd_seed_demo
    )
    sub.add_parser("stats", help="what's in the database").set_defaults(func=cmd_stats)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)  # its debug logs would show URLs (token!)
    try:
        args.func(args)
    except (KiakiaError, ConfigError) as exc:
        print(f"Error: {exc}", file=sys.stderr)  # clear message, no stack trace, no secrets
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
