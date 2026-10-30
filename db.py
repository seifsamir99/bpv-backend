"""
Supabase Postgres connection layer.

The app connects through Supabase's Supavisor pooler in TRANSACTION mode
(port 6543) — the free tier's direct connection (db.<ref>.supabase.co:5432)
is IPv6-only, which Railway and most home networks can't reach. psycopg2 is
compatible with transaction pooling (it doesn't use named prepared
statements).

Env var: SUPABASE_DB_URL
  postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:6543/postgres

Usage:
    from db import q, q_one, execute, execute_many, transaction

    rows = q("SELECT * FROM employees WHERE type = %s", ('Labour',))
    execute("UPDATE employees SET ot_hours = %s WHERE id = %s", (5, '12'))

    # multi-statement atomic transaction (retried as a whole if the pooled
    # connection turned out to be stale, so fn must not have side effects
    # outside the database):
    def fn(conn):
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bpv_line_items WHERE bpv_no = %s", ('7',))
            cur.execute("INSERT INTO bpv_line_items (bpv_no) VALUES (%s)", ('7',))
    transaction(fn)

Stale connections: Supavisor (and long idle periods) can silently drop pooled
client connections. Rather than pinging before every checkout (an extra
round-trip per query), a failed operation discards its connection and retries
on a fresh one -- see _is_stale_error and _with_retry, both of which are
narrower/wider than they look for reasons the 2026-08-20 outage taught us.
"""

import os
import threading
from contextlib import contextmanager

import psycopg2
import psycopg2.pool
from psycopg2 import OperationalError, InterfaceError, DatabaseError
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

_pool = None
_pool_lock = threading.Lock()

# Kept in one place: _with_retry needs to know how many dead connections it
# might have to chew through before the pool hands out a fresh one.
_POOL_SIZE = 8


# Substrings of the libpq messages that mean "this connection is dead", as
# opposed to "your statement was bad". Matched case-insensitively.
_STALE_HINTS = (
    'server closed the connection unexpectedly',
    'ssl connection has been closed unexpectedly',
    'connection already closed',
    'connection not open',
    'could not receive data from server',
    'could not send data to server',
    'terminating connection',
    'no connection to the server',
    'eof detected',
    'consuming input failed',
    'server conn crashed',
    'connection reset by peer',
)


def _is_stale_error(exc):
    """True if exc means the pooled connection is dead, not that the SQL was bad.

    psycopg2 does not use a single class for this. A dropped connection raises
    OperationalError in some paths, but on the Railway build the very same
    Supabase drop arrived as a *bare* psycopg2.DatabaseError -- when libpq
    reports no SQLSTATE, psycopg2 falls back to the base class. DatabaseError is
    the PARENT of OperationalError, so `except (OperationalError,
    InterfaceError)` silently missed it and the outage never self-healed.

    So: trust the class where it is specific, and fall back to the message only
    for the two generic base classes. Concrete SQL-error subclasses
    (IntegrityError, ProgrammingError, ...) must never match -- retrying those
    would just replay a broken statement.
    """
    if isinstance(exc, (OperationalError, InterfaceError)):
        return True
    if type(exc) in (DatabaseError, psycopg2.Error):
        msg = str(exc).lower()
        return any(hint in msg for hint in _STALE_HINTS)
    return False


def _get_pool():
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                dsn = os.getenv('SUPABASE_DB_URL')
                if not dsn:
                    raise RuntimeError("SUPABASE_DB_URL is not set")
                # Fixed-size pool (minconn == maxconn): psycopg2's pool CLOSES
                # any returned connection beyond minconn and re-opens new ones
                # inside the pool lock — with a remote DB that serialized every
                # burst of parallel requests into seconds of TLS handshakes.
                # Keeping all 8 warm makes bursts (the payroll page) instant;
                # the one-time cost is ~8 connections opened on first use.
                _pool = psycopg2.pool.ThreadedConnectionPool(
                    minconn=_POOL_SIZE, maxconn=_POOL_SIZE, dsn=dsn
                )
    return _pool


@contextmanager
def get_conn(transactional=True):
    """Check out a pooled connection; commit on success, rollback on error.

    transactional=False runs in autocommit mode: each statement is its own
    implicit transaction, which saves the BEGIN and COMMIT network round-trips
    (the database is remote — that's 3 round-trips down to 1 for a SELECT).
    Use it for single reads/writes; keep the default for multi-statement work.

    A connection that died (see _is_stale_error) is closed on return so the
    pool replaces it instead of handing it out again.
    """
    pool = _get_pool()
    conn = pool.getconn()
    broken = False
    try:
        conn.autocommit = not transactional
        yield conn
        if transactional:
            conn.commit()
    except Exception as exc:
        broken = _is_stale_error(exc)
        if transactional and not broken:
            try:
                conn.rollback()
            except Exception:
                broken = True
        raise
    finally:
        # Restoring the pool default can itself fail on a dead socket; if it
        # does, the connection is unusable rather than merely dirty. This used
        # to share a try block with putconn, so such a failure skipped putconn
        # entirely and leaked the connection out of the pool for good -- after
        # _POOL_SIZE of them the pool raised "connection pool exhausted".
        if not broken:
            try:
                if not conn.closed:
                    conn.autocommit = False
            except Exception:
                broken = True
        try:
            pool.putconn(conn, close=broken or bool(conn.closed))
        except Exception:
            pass


def _with_retry(fn):
    """Run fn, retrying while the connections it draws turn out to be stale.

    Retrying *once* is not enough. The pool holds _POOL_SIZE warm connections
    and the things that kill them -- a Supabase restart, an idle reap -- kill
    every one at the same moment, so a single retry just draws the next corpse.
    Each failed attempt discards its connection (get_conn closes it), so after
    at most _POOL_SIZE failures the pool has to open a fresh one.

    Only stale-connection failures are retried; real SQL errors propagate on
    the first attempt.
    """
    last_exc = None
    for _ in range(_POOL_SIZE + 1):
        try:
            return fn()
        except Exception as exc:
            if not _is_stale_error(exc):
                raise
            last_exc = exc
    raise last_exc


def q(sql, params=None):
    """Run a SELECT (or anything RETURNING rows); return list of dicts."""
    def run():
        with get_conn(transactional=False) as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(sql, params)
                return [dict(r) for r in cur.fetchall()]
    return _with_retry(run)


def q_one(sql, params=None):
    """Run a query expected to return at most one row; return dict or None."""
    rows = q(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    """Run a single statement (its own implicit transaction); return row count."""
    def run():
        with get_conn(transactional=False) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.rowcount
    return _with_retry(run)


def execute_many(sql, param_list):
    """Run the same statement for many parameter tuples in one transaction."""
    if not param_list:
        return 0
    def run():
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.executemany(sql, param_list)
                return cur.rowcount
    return _with_retry(run)


def transaction(fn):
    """Run fn(conn) inside a single committed transaction, with stale retry.

    fn must be safe to re-run (it is retried in full if the first pooled
    connection was stale — the failed attempt is rolled back).
    """
    def run():
        with get_conn() as conn:
            return fn(conn)
    return _with_retry(run)
