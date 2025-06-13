# CunningBot

CunningBot is a full-featured Discord bot powered by OpenAI.  It provides natural-language chat, image generation, and summarisation commands while allowing guild administrators to customize the bot's **default persona** at runtime.  The project is designed to be easy to run locally or inside Docker and is ready for deployment to Raspberry Pi or any Linux host.

---

## Core Features

| Slash Command | Description |
|---------------|-------------|
| `/chat` | Chat with the LLM about anything.  Supports model selection, message-history window size, persona selection, and private replies. |
| `/summarize` | Generate a concise summary of the last *n* messages in the channel, mentioning each participant. |
| `/image` | Create an image from a text prompt using OpenAI's DALL-E API. |
| `/persona default [persona]` | Set or view the default persona for this guild. |
| `/persona list` | List all available personas with descriptions. |
| `/baseball agent` | Ask factual questions about baseball. |
| `/daily-game` | Manage automated daily game reminders (see [Daily Game System](#daily-game-system)). |

## Daily Game System

CunningBot can automatically post daily game reminders to Discord channels at scheduled times. The system runs every 10 minutes and posts games at their scheduled Pacific time slots.

### Daily Game Commands

| Command | Description | Permission Required |
|---------|-------------|-------------------|
| `/daily-game register` | Register a new daily game or update an existing one | Administrator |
| `/daily-game list` | List all registered daily games for this server | None |
| `/daily-game enable` | Enable a disabled daily game | Administrator |
| `/daily-game disable` | Temporarily disable a daily game without deleting it | Administrator |
| `/daily-game delete` | Permanently delete a registered daily game | Administrator |
| `/daily-game preview` | Preview what a daily game message will look like | None |

### Usage Examples

**Register a new daily game:**
```
/daily-game register name:Wordle link:https://www.nytimes.com/games/wordle hour:9 minute:30
```

**List all games:**
```
/daily-game list
```

**Disable a game temporarily:**
```
/daily-game disable name:Wordle
```

**Delete a game permanently:**
```
/daily-game delete name:Wordle
```

### How It Works

1. **Registration**: Administrators can register games with a name, URL, and Pacific time schedule
2. **Scheduling**: Games are scheduled in 10-minute intervals (e.g., 9:00, 9:10, 9:20, etc.)
3. **Posting**: At the scheduled time, the bot posts the game link to the specified channel
4. **Threading**: Messages are automatically organized into daily threads to keep channels clean
5. **Persistence**: Game settings are saved and persist between bot restarts

### Features

- **Channel-specific**: Each game is tied to a specific Discord channel
- **Time zones**: All scheduling uses Pacific time for consistency
- **Thread creation**: Automatically creates daily threads for each game
- **Duplicate handling**: Games with the same name in the same channel will update the existing game
- **Cross-channel protection**: Prevents duplicate game names across different channels
- **Enable/disable**: Games can be temporarily disabled without losing settings

### Technical Details

- The daily game poster runs as a separate Docker service (`dailygame`)
- Games are checked every 10 minutes and posted at their scheduled time
- State is persisted in `bot/domain/app_state.json`
- All times are in Pacific timezone (`America/Los_Angeles`)

## Available Personas

The bot supports multiple personas that change its behavior and response style:

| Persona | Description |
|---------|-------------|
| **A discord user** (`discord_user`) | *Default* - Casual, friendly chat style suitable for Discord conversations |
| **Cat** (`cat`) | Responds like a literal cat with meows, purrs, and cat-like behavior |
| **Helpful Assistant** (`helpful_assistant`) | Professional, informative assistance style |
| **Sarcastic Jerk** (`sarcastic_jerk`) | Responds with sarcasm and attitude |
| **Homer Simpson** (`homer_simpson`) | Method actor playing Homer Simpson character |

### Persona System

- **Global Default**: All guilds use "A discord user" persona by default
- **Guild-Specific**: Configured guilds can set their own default persona
- **Per-Chat Override**: Individual `/chat` commands can specify a different persona
- **Access Control**: Only properly configured guilds can change default personas

## Guild Configuration

CunningBot uses a guild configuration system to control which Discord servers can modify bot settings:

- **Configured Guilds**: Can use `/persona default` to set custom default personas
- **Unconfigured Guilds**: Use the global default persona ("A discord user") and cannot change settings
- **Configuration File**: Guild access is controlled via `.guild_config.json` (see setup instructions)
- **Error Handling**: Unconfigured guilds receive clear error messages when attempting to change settings

This system ensures that only authorized servers can modify the bot's behavior while maintaining a consistent default experience.

Additional helper utilities include message splitting to respect Discord's 2 000-character limit and rich structured logging.

## Project Layout

```
├── bot/                     # Source code
│   ├── api/                 # Third-party service clients
│   ├── commands/            # Discord Cogs (slash commands)
│   ├── domain/              # Domain & state-management services
│   ├── listeners/           # Event listeners (message, reaction, …)
│   ├── utils.py             # Generic helpers
│   └── main.py              # Application entry-point
├── generated_images/        # Saved images from `/image`
├── logs/                    # Rotating json logs
├── tests/                   # PyTest suite
├── Dockerfile               # Production container image
├── docker-compose.yml       # 1-click local deployment
├── requirements.txt         # Python dependencies (locked versions)
├── Makefile                 # Common dev & ops tasks
```

## Installation

### 1. Clone & create your `.env`

```bash
cp .env.example .env
# Edit the file and fill in real values
```

Required keys:

| Variable           | Purpose                              |
|--------------------|--------------------------------------|
| `DISCORD_TOKEN`    | Bot token from the Discord Developer Portal |
| `CLIENT_ID`        | Application / Client ID (used for invite URL) |
| `OPENAI_API_KEY`   | OpenAI secret key                    |
| `GUILD_ID`         | *(optional)* Restrict command sync to one guild |

### 2. Native (Python ≥ 3.11)

```bash
python -m venv .venv
source .venv/bin/activate
make install         # ⇢ pip install -r requirements.txt
make run             # ⇢ python -m bot.main
```

### 3. Docker / Docker-Compose

```bash
make build   # Build image (or `docker-compose build`)
make start   # Run in background
make logs    # Tail container logs
```

The image runs as an unprivileged `appuser` and stores data in *logs/*, *generated_images/* and *bot/domain/app_state.json* which can be mounted on the host if desired.

## Development

* **Formatting** – [black](https://black.readthedocs.io/) & [isort](https://pycqa.github.io/isort/)
* **Type Checking** – `mypy` (strict settings configured in *mypy.ini*)
* **Linting** – `ruff` (optional)
* **Tests** – `pytest`

Typical workflow:

```bash
pytest          # run tests
mypy bot        # static type checks
```

### Adding New Slash Commands

Create a new Cog under *bot/commands/*.  Register your command with `@app_commands.command` and add the cog in its own `setup` coroutine.

```python
class HelloCog(commands.Cog):
    @app_commands.command(name="hello")
    async def hello(self, interaction: discord.Interaction):
        await interaction.response.send_message("Hello world!")

async def setup(bot):
    await bot.add_cog(HelloCog())
```

The bot auto-loads every `*.py` file in that directory when starting.


## Logging

Structured JSON logs are written to *logs/cunningbot-YYYY-MM-DD.json* (date-rotated).  Adjust verbosity or format by editing *bot/domain/logger.py*.

## Testing

```bash
pytest -q             # run all tests quietly
```

CI pipelines should run `pytest` and `mypy` to ensure correctness and maintain strict typing.

## Deployment Notes

* The container image is based on `python:3.11-slim`.
* A non-root user (UID 1000) is created for safer execution (ideal for Raspberry Pi).
* Volume-mount *logs/*, *generated_images/* and *bot/domain/app_state.json* if you need persistent data.

## Contributing

Pull requests are welcome!  Please ensure all existing tests pass and include new tests for any changed functionality.

## License

This project is licensed under the MIT License – see `LICENSE` for details.
