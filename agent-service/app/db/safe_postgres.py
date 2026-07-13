from __future__ import annotations

import asyncio

import pandas as pd
import psycopg
from psycopg.rows import dict_row


class SafePostgresRunner:
    def __init__(self, connection_string: str, statement_timeout_ms: int = 5000):
        self.connection_string = connection_string
        self.statement_timeout_ms = statement_timeout_ms

    async def run(self, sql: str) -> pd.DataFrame:
        return await asyncio.to_thread(self._run_sync, sql)

    def _run_sync(self, sql: str) -> pd.DataFrame:
        with psycopg.connect(
            self.connection_string,
            row_factory=dict_row,
            options=f"-c default_transaction_read_only=on -c statement_timeout={self.statement_timeout_ms}",
        ) as connection:
            with connection.transaction():
                with connection.cursor() as cursor:
                    cursor.execute(sql)
                    rows = cursor.fetchall()
                    return pd.DataFrame(rows)

