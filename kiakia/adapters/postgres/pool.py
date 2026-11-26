"""A pool of open database connections for the web API (review finding M4b).

Opening a PostgreSQL connection costs a network round trip plus password checking: about 55 ms
on a laptop with Docker, measured. The web app polls the API every few seconds, so paying that
on EVERY request made the whole inbox slower. The pool keeps a few connections open and lends
one to each request instead (about 4 ms for a simple query).

- Same settings as single connections (autocommit, statement_timeout): `connection_kwargs`.
- Each borrowed connection is checked first, so after a database restart a dead connection is
  replaced instead of failing the request.
- If no connection is free within a few seconds (database down or overloaded) the caller gets
  StorageUnavailable, so the API answers 503 "try again", as it does for a single connection.
"""

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool, PoolTimeout

from kiakia.domain.errors import StorageUnavailable

from .repository import connection_kwargs


def open_pool(
    host: str,
    port: int,
    user: str,
    password: str,
    dbname: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
    wait_seconds: float = 5.0,
) -> ConnectionPool[psycopg.Connection]:
    return ConnectionPool(
        kwargs=connection_kwargs(host, port, user, password, dbname),
        min_size=min_size,
        max_size=max_size,
        timeout=wait_seconds,
        check=ConnectionPool.check_connection,
        name="kiakia-api",
        open=True,
    )


@contextmanager
def borrow(pool: ConnectionPool[psycopg.Connection]) -> Iterator[psycopg.Connection]:
    """Lend one connection for the length of the block, then give it back to the pool."""
    try:
        conn = pool.getconn()
    except PoolTimeout as exc:
        raise StorageUnavailable(f"no database connection available: {exc}") from exc
    try:
        yield conn
    finally:
        # Always returned, even after an error. The pool rolls back anything left half-done
        # and throws away a connection that broke, so the next request never inherits it.
        pool.putconn(conn)
