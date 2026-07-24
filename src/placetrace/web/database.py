"""
Database initialization and per-request connection management
"""

from flask import g
from flask_sqlalchemy import SQLAlchemy

from placetrace.db import get_main_connection

# Legacy SQLAlchemy handle; removed once the last route is converted to psycopg
db = SQLAlchemy()


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
    """Initialize database with Flask app"""
    db.init_app(app)
    app.teardown_appcontext(close_db)
