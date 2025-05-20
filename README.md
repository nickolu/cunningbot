# ManchatBot

ManchatBot is a full-featured Discord bot powered by OpenAI.  It provides natural-language chat, image generation, and summarisation commands while allowing the guild owner to customise the bot’s **personality** at runtime.  The project is designed to be easy to run locally or inside Docker and is ready for deployment to Raspberry Pi or any Linux host.

---

## Core Features

| Slash Command | Description |
|---------------|-------------|
| `/chat` | Chat with the LLM about anything.  Supports model selection, message-history window size, and private replies. |
| `/summarize` | Generate a concise summary of the last *n* messages in the channel, mentioning each participant. |
| `/image` | Create an image from a text prompt using OpenAI’s DALL-E API. |
| `/settings personality` | Get, set, or clear the bot’s personality.  The personality text is persisted across restarts. |

Additional helper utilities include message splitting to respect Discord’s 2 000-character limit and rich structured logging.

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

## Personality Service

The personality is stored in *bot/domain/app_state.json* and accessed through the `personality_service`.  It is automatically loaded on startup and saved every time you call `/settings personality`.

```bash
/settings personality "a sarcastic but helpful assistant"
```

Pass `get` to the command to see the current setting or leave the argument empty to clear it.

## Logging

Structured JSON logs are written to *logs/manchatbot-YYYY-MM-DD.json* (date-rotated).  Adjust verbosity or format by editing *bot/domain/logger.py*.

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
