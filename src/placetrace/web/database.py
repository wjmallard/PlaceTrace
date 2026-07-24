"""
Per-request database connection management
"""

from flask import g

from placetrace.db import get_main_connection


def get_db():
    """Return this request's psycopg connection, opening it on first use."""
    if 'db_conn' not in g:
        g.db_conn = get_main_connection()
    return g.db_conn


def close_db(exc=None):
    """Close the request connection, if one was opened."""
    conn = g.pop('db_conn', None)
    if conn is not None:
        conn.close()


def init_db(app):
    """Register per-request connection teardown"""
    app.teardown_appcontext(close_db)
