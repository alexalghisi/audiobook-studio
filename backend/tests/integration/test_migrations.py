import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ..conftest import DATABASE_URL

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]


async def test_migrations_build_the_schema_the_models_describe() -> None:
    base = DATABASE_URL.rsplit("/", 1)[0]
    name = f"studio_migrations_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(f"{base}/postgres", isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        env = {**os.environ, "STUDIO_DATABASE_URL": f"{base}/{name}"}
        alembic = [sys.executable, "-m", "alembic"]
        subprocess.run([*alembic, "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)
        # `alembic check` fails if autogenerate would produce any operation.
        check = subprocess.run([*alembic, "check"], cwd=BACKEND, env=env, capture_output=True, text=True)
        assert check.returncode == 0, check.stdout + check.stderr
        subprocess.run([*alembic, "downgrade", "base"], cwd=BACKEND, env=env, check=True, capture_output=True)
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
        await admin.dispose()
