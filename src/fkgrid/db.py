"""Short-lived MySQL connections against the live catalog/cart database.

No connection pooling, no fixtures. One connection per call, matching the
transactional boundaries used by catalog.py and cart.py.
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from typing import Iterator

import pymysql
import pymysql.cursors

from .config import MySQLConfig, load_mysql_config


@functools.lru_cache(maxsize=1)
def _config() -> MySQLConfig:
    return load_mysql_config()


def connect() -> pymysql.connections.Connection:
    config = _config()
    return pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


@contextmanager
def connection() -> Iterator[pymysql.connections.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[pymysql.cursors.DictCursor]:
    conn = connect()
    try:
        with conn.cursor() as cursor:
            yield cursor
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
