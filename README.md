# Noa — Discord study companion bot

Noa tracks learning roadmaps, DSA revision, coding-platform stats (Codeforces,
LeetCode, AtCoder), streaks and server leaderboards. Built with discord.py 2.x.

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
# edit .env and set DISCORD_TOKEN
```

Python 3.12+ recommended (uses `zoneinfo` + `tzdata`).

## Environment variables

| Variable        | Required | Default       | What it does                                    |
|-----------------|----------|---------------|-------------------------------------------------|
| `DISCORD_TOKEN` | yes      | —             | Bot token. The bot refuses to start without it. |
| `BOT_PREFIX`    | no       | `>`           | Command prefix.                                 |
| `BOT_TZ`        | no       | `Asia/Kolkata`| Timezone for weeks, streaks and snapshots.      |
| `NOA_DB_PATH`   | no       | `data/noa.db` | SQLite file. Point it at a temp file for tests. |
| `OWNER_ID`      | no       | —             | Your Discord user ID. Enables `>admin` commands.|
| `LOG_LEVEL`     | no       | `INFO`        | `DEBUG`, `INFO`, `WARNING`, `ERROR`.            |
| `REPO_URL`      | no       | —             | Link shown in `>about`. Leave empty to hide it. |
| `CONTACT`       | no       | —             | Contact shown in the privacy policy and consent.|
| `PRIVACY_URL`   | no       | —             | Link to the full privacy policy, if you have one.|
| `MIN_ACCOUNT_AGE_DAYS` | no | `0`          | Minimum Discord account age to register (`0` = off).|

Also enable the **Message Content** and **Server Members** privileged intents
for the application in the Discord Developer Portal, or login will fail.

## Run

```powershell
.\venv\Scripts\Activate.ps1
python main.py
```

Logs go to `logs/noa.log` (rotated) and the console. Daily SQLite backups land
in `backups/` (last 7 kept). Both directories are git-ignored, as are `.env`
and the database itself.

## Tests

Schema/link validators (no database needed):

```powershell
python scripts/check_roadmaps.py
python scripts/check_dsa.py
```

Any script that touches the database must run against a throwaway copy:

```powershell
$env:NOA_DB_PATH = "$env:TEMP\noa-test.db"; .\venv\Scripts\python.exe <script>; $env:NOA_DB_PATH = $null
```

or in Python:

```python
import db
with db.temp_db():
    ...  # every db call here hits an empty temp database
```

`db.temp_db()` (and the `NOA_TESTING` flag it sets) guarantees test code can
never touch the real `data/noa.db`.

## Notable commands

- `>bio <text>` — set a 150-char profile bio (mentions and links are stripped); `>bio clear` removes it.
- `>ping` — heartbeat, API round-trip and database latency in one embed.
- `>about` — version, uptime, servers, users, and repo link.
- `>help [topic]` — category overview, or details/examples for one category or command.
- `>register` / `>agree` — privacy consent comes first; declining stores nothing.
- `>privacy` — what Noa stores, your consent version, plus `>export` (JSON copy, hourly cooldown) and `>deleteme`.
- `>admin stats|sync|announce-now` — owner only, hidden from `>help`.

## Points and ranks

Solves earn points by difficulty: Easy 1 · Medium 3 · Hard 10. Lifetime
points across verified linked platforms set your rank:

Acolyte (0+) · Sentinel (250+) · Warden (600+) · Centurion (1200+) ·
Archon (2200+) · Titan (3500+)

The Problems leaderboard, weekly podium and announcements all rank by
points for the period. `>ranks` shows every rank plus your progress to
the next one. Set `USE_CUSTOM_EMOJIS=false` in `.env` for plain-text
rank, platform and streak markers instead of custom emojis.
