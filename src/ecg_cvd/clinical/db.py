"""Small SQLAlchemy integration without coupling the legacy workbench to a DB."""
from __future__ import annotations

from typing import Iterator

from flask import Flask, current_app
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.orm import DeclarativeBase, Session, scoped_session, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, database_url: str):
        connect_args: dict[str, object] = {}
        if database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
        self.engine: Engine = create_engine(database_url, future=True, pool_pre_ping=True,
                                            connect_args=connect_args)
        if database_url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_foreign_keys)
        self.session_factory = scoped_session(sessionmaker(bind=self.engine, autoflush=False,
                                                           expire_on_commit=False, future=True))

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    def init_app(self, app: Flask) -> None:
        app.extensions["ecg_platform_db"] = self

        @app.teardown_appcontext
        def remove_session(_exception: BaseException | None = None) -> None:
            self.session_factory.remove()

    def session(self) -> Session:
        return self.session_factory()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)


def get_db() -> Database:
    return current_app.extensions["ecg_platform_db"]


def get_session() -> Session:
    return get_db().session()
