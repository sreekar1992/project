"""Alembic environment for PostgreSQL/SQLite platform schema upgrades."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from ecg_cvd.clinical.config import Settings
from ecg_cvd.clinical.db import Base
import ecg_cvd.clinical.models  # noqa: F401 - registers every mapped table


config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
platform_settings = Settings.from_environment()
config.set_main_option("sqlalchemy.url", platform_settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=platform_settings.database_url, target_metadata=target_metadata,
                      literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.",
                                     poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          compare_type=True, compare_server_default=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
