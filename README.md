# OutBot

A modular Discord assistant tailored for community management. The bot handles booster monitoring, direct-message relays, voice-channel stickiness, presence tracking, and a few quality-of-life slash commands.

## Features
- **Booster automation** – assigns special roles when members join with a booster invite; periodically reports/kicks lapsed boosters.
- **DM relay** – forwards user DMs to the admin, allows quick replies, and keeps ticket identifiers for each user.
- **Voice channel stickiness** – keeps the bot in a chosen voice channel, handles reconnection attempts with exponential backoff, and auto-mutes.
- **Presence tracking** – mirrors the presence of a tracked user and toggles the bot’s status accordingly.
- **Slash commands** – quick access to invite links, movie sheets, TMDB image sharing, custom status, etc.
- **Target mini-game** – prefix commands `!target` / `!go` for short opt-in giveaways.

## Requirements
- Python 3.10+
- Dependencies listed in `requirements.txt`
- Discord bot token with privileged intents (members, presences, message content) enabled

## Getting Started
1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd outbot
   ```
2. **Create a virtual environment (optional, but recommended)**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```
3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
4. **Configure environment variables**
   - Copy `.env.example` to `.env`
     ```bash
     cp .env.example .env
     ```
   - Fill in the Discord token, admin ID, guild/channel IDs, and optional database credentials.

## Running the Bot
```bash
python outbot.py
```
The bot automatically syncs its application commands with the configured guild on startup.

## Key Commands
| Type | Command | Description |
| --- | --- | --- |
| Slash | `/фильмы` | Shares the private movies spreadsheet (role-gated). |
| Slash | `/invite` | Returns the special booster invite link. |
| Slash | `/status` | Updates the bot presence and activity (admin only). |
| Slash | `/track` | Toggles presence tracking for the configured user (admin only). |
| Slash | `/накрутка` / `/стопнакрутка` | Pins/unpins the bot to the caller’s voice channel. |
| Slash | `/dm` | Reply to a user by ticket or ID via DM relay (admin only). |
| Prefix | `!target` / `!go` | Starts or stops the target mini-game. |

## Project Structure
```
outbot.py          # Entry point
bot/
  bot.py           # Bot factory and common settings
  utils.py         # Shared utilities (admin notifications, logging)
  cogs/            # Feature-specific cogs
    boosters.py
    dm_relay.py
    error_handlers.py
    misc.py
    target_game.py
    tracking.py
    voice.py
config.py          # Environment-driven configuration loader
.env.example       # Template for required environment variables
```

## Publishing to GitHub
1. Create an empty repository on GitHub (or any git hosting provider).
2. Add it as a remote and push the history:
   ```bash
   git remote add origin git@github.com:<user>/<repo>.git
   git push -u origin master
   ```
3. Update the remote URL later with `git remote set-url origin <new-url>` if needed.

## Contributing
- Use separate branches for features/fixes.
- Run `python3 -m compileall bot config.py outbot.py` before committing to catch syntax errors quickly.
- Keep secrets in `.env`; never commit them.

Enjoy automating your Discord community!
