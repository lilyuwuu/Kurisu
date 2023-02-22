from datetime import datetime, timedelta
from typing import NamedTuple, TYPE_CHECKING

import asyncpg
from discord.utils import time_snowflake, snowflake_time

from .common import BaseDatabaseManager

if TYPE_CHECKING:
    from typing import Tuple, AsyncGenerator, Optional


class WarnEntry(NamedTuple):
    warn_id: int
    user_id: int
    issuer_id: int
    date: datetime
    reason: str


class DeletedWarnEntry(NamedTuple):
    warn_id: int
    user_id: int
    issuer_id: int
    date: datetime
    reason: str
    deletion_time: datetime
    deletion_reason: str
    deleter: int


tables = {'warns': ['id', 'user_id', 'issuer_id', 'reason', 'deletion_time', 'deletion_reason', 'deleter']}


class WarnsDatabaseManager(BaseDatabaseManager, tables=tables):
    """Manages the warns database."""

    async def add_warning(self, user_id: int, issuer: int, reason: 'Optional[str]') -> 'Tuple[int, int]':
        """Add a warning to the user id."""
        assert isinstance(user_id, int), type(user_id)
        assert isinstance(reason, (str, type(None))), type(str)
        await self.bot.configuration.add_member(user_id)
        now = time_snowflake(datetime.now())
        await self._insert('warns', id=now, user_id=user_id, issuer_id=issuer, reason=reason)
        self.log.debug('Added warning %d to user id %d, %r', now, user_id, reason)
        count = await self.get_warnings_count(user_id=user_id)
        return now, count

    async def get_warnings(self, user_id: int) -> 'AsyncGenerator[WarnEntry, None]':
        """Get warnings for a user id."""
        assert isinstance(user_id, int)
        conn: asyncpg.Connection

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                query = 'SELECT id, user_id, issuer_id, reason FROM warns WHERE user_id = $1 and deleter is NULL'
                async for warn_id, w_user_id, issuer, reason in conn.cursor(query, user_id):
                    yield WarnEntry(user_id=w_user_id,
                                    warn_id=warn_id,
                                    date=snowflake_time(warn_id),
                                    issuer_id=issuer,
                                    reason=reason)

    async def get_deleted_warnings(self, user_id: int) -> 'AsyncGenerator[DeletedWarnEntry, None]':
        """Get warnings for a user id."""
        assert isinstance(user_id, int)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                query = 'SELECT * FROM warns WHERE user_id = $1 and deleter is not NULL'
                async for warn_id, w_user_id, issuer, reason, deletion_time, deletion_reason, deleter in conn.cursor(
                        query, user_id):
                    yield DeletedWarnEntry(user_id=w_user_id,
                                           warn_id=warn_id,
                                           date=snowflake_time(warn_id),
                                           issuer_id=issuer,
                                           reason=reason,
                                           deletion_reason=deletion_reason,
                                           deletion_time=deletion_time,
                                           deleter=deleter)

    async def get_warning(self, warn_id: int) -> 'Optional[WarnEntry]':
        """Get a specific warning based on warn id."""
        try:
            res = await self._select('warns', warn_id=warn_id).__anext__()
        except StopIteration:
            return
        return WarnEntry(user_id=res[1],
                         warn_id=res[0],
                         date=snowflake_time(res[0]),
                         issuer_id=res[2],
                         reason=res[3])

    async def get_warnings_count(self, user_id: int) -> int:
        """Get a specific warning based on warn id."""
        assert isinstance(user_id, int)
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                query = 'SELECT COUNT(*) FROM warns WHERE user_id = $1 AND deleter is NULL'
                record = await conn.fetchrow(query, user_id)
        return record[0]

    async def delete_warning(self, warn_id: int, deleter: int, reason: 'Optional[str]') -> int:
        """Remove a warning based on warn id."""
        assert isinstance(warn_id, int)
        res = await self._update('warns',
                                 {'deletion_time': datetime.now(), 'deletion_reason': reason, 'deleter': deleter},
                                 id=warn_id)
        if res:
            self.log.debug('Removed warning %d', warn_id)
        return res

    async def delete_deleted_warning(self, warn_id: int) -> int:
        """Remove a warning based on warn id."""
        assert isinstance(warn_id, int)
        res = await self._delete('warns', id=warn_id)
        return res

    async def delete_all_warnings(self, user_id: int, deleter: int, reason: 'Optional[str]') -> int:
        """Delete all warnings for a user id."""
        assert isinstance(user_id, int)
        res = await self._update('warns',
                                 {'deletion_time': datetime.now(), 'deletion_reason': reason, 'deleter': deleter},
                                 user_id=user_id)
        if res:
            self.log.debug('Removed all warnings for %d', user_id)
        return res

    async def copy_all_warnings(self, origin: int, destination: int):
        """Copies all warnings from a user id to another user id"""
        warns = []
        await self.bot.configuration.add_member(destination)
        assert (await self.get_warnings_count(origin)) + (await self.get_warnings_count(destination)) <= 5

        async for w in self.get_warnings(origin):
            snowflake = w.warn_id
            while snowflake == w.warn_id:
                time = snowflake_time(snowflake)
                snowflake = time_snowflake(time + timedelta(milliseconds=1))
            warns.append((snowflake, destination, w.issuer_id, w.reason))
        query = "INSERT INTO warns VALUES ($1,$2,$3,$4) ON CONFLICT (id) DO UPDATE SET id = excluded.id+1"
        conn: asyncpg.Connection
        try:
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.executemany(query, warns)
        except asyncpg.UniqueViolationError:
            self.log.error("Error when copying warns", exc_info=True)
            return 0
        return len(warns)
