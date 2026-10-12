"""Daily SQLite backups via the online backup API. Keeps the last 7."""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from discord.ext import commands, tasks

import db

logger = logging.getLogger(__name__)
KEEP = 7


def backup_database(db_path: Path | None = None, backups_dir: Path | None = None) -> Path | None:
    """Copy the live DB with sqlite3's online backup API. Returns the new file, or None."""
    src_path = db_path or db.get_db_path()
    if not src_path.exists():
        logger.warning("Skipping backup: database file %s does not exist yet", src_path)
        return None
    dest_dir = backups_dir or (Path(__file__).parent.parent / "backups")
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = dest_dir / f"noa-{stamp}.db"
    src = sqlite3.connect(src_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    for old in sorted(dest_dir.glob("noa-*.db"))[:-KEEP]:
        try:
            old.unlink()
        except OSError:
            logger.warning("Could not prune old backup %s", old)
    logger.info("Database backup written to %s", dest)
    return dest


class Backups(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.backup_loop.start()

    def cog_unload(self) -> None:
        self.backup_loop.cancel()

    @tasks.loop(hours=24)
    async def backup_loop(self) -> None:
        try:
            backup_database()
        except Exception:  # noqa: BLE001 - backups must never crash the bot
            logger.exception("Database backup failed")

    @backup_loop.before_loop
    async def _before_backup_loop(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Backups(bot))
