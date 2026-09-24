"""Controlled schema migration plus idempotent platform-reference bootstrap."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import tempfile

from alembic import command
from alembic.config import Config

# Migration/bootstrap does not need plotting, but reference-data imports share
# modules with the existing explainability adapter. Give Matplotlib a writable
# cache before those imports so a controlled release job stays non-interactive.
os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "ecg-platform-matplotlib"))

from .config import Settings
from .db import Database
from .services import initialize_reference_data


def migrate_and_bootstrap(project_root: Path) -> None:
    """Apply reviewed migrations, then create only role/permission/feature rows.

    This command deliberately creates no users, hospitals, patients, or sample
    clinical records. Initial administrators must be provisioned through an
    approved operational process rather than a baked-in credential.
    """
    root = project_root.resolve()
    alembic_config = Config(str(root / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(root / "migrations"))
    command.upgrade(alembic_config, "head")
    settings = Settings.from_environment({"PROJECT_ROOT": root})
    database = Database(settings.database_url)
    try:
        initialize_reference_data(database.session())
    finally:
        database.session_factory.remove()


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate and bootstrap ECG platform reference data.")
    parser.add_argument("--project", type=Path, default=Path.cwd(), help="Repository/application root")
    args = parser.parse_args()
    migrate_and_bootstrap(args.project)
    print("Schema migrations and platform reference data are ready. No users or patient data were created.")


if __name__ == "__main__":
    main()
