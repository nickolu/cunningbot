# CunningBot

CunningBot is a full-featured Discord bot powered by OpenAI.  It provides natural-language chat, image generation, and summarisation commands while allowing guild administrators to customize the bot's **default persona** at runtime.  The project is designed to be easy to run locally or inside Docker and is ready for deployment to Raspberry Pi or any Linux host.

---

## Core Features

| Slash Command | Description |
|---------------|-------------|
| `/chat` | Chat with the LLM about anything.  Supports model selection, message-history window size, persona selection, and private replies. |
| `/image` | Create an image from a text prompt using OpenAI's DALL-E API. |
| `/image-json` | Create highly specific images using structured photography parameters formatted as JSON. |
| `/roll` | Roll dice using expressions like `4d6`, `1d20+5`, or `d20`. Defaults to 1d20 if no expression provided. |
| `/persona default [persona]` | Set or view the default persona for the chat in this guild. |
| `/persona list` | List all available personas with descriptions. |
| `/baseball agent` | Ask factual questions about baseball. |
| `/daily-game` | Manage automated daily game reminders and view participation statistics (see [Daily Game System](#daily-game-system)). |

## Structured Image Generation

The `/image-json` command allows you to create highly specific images by defining structured photography parameters that get formatted as JSON and passed to the image generation API. This gives you precise control over technical aspects like camera settings, lighting, and composition.

### Available Parameters

| Parameter | Description | Examples |
|-----------|-------------|----------|
| **json_string** | Raw JSON string with any image parameters | `{"filter":"prism","mood":"dramatic"}`, `{"subject":"car","style":"cinematic"}` |
| **subject** | The main subject of the image | `"a red sports car driving down the road"`, `"portrait of a woman"` |
| **lighting** | Lighting conditions | `"street lights at night"`, `"golden hour"`, `"studio lighting"` |
| **focal_length** | Camera focal length | `"85mm"`, `"24mm"`, `"200mm"` |
| **aperture** | Camera aperture | `"f/1.4"`, `"f/2.8"`, `"f/8"` |
| **shutter_speed** | Camera shutter speed | `"1/1000"`, `"1/60"`, `"1s"` |
| **style** | Photography/art style | `"sports photography"`, `"portrait photography"`, `"cinematic"` |
| **camera** | Camera model or type | `"Canon EOS R5"`, `"film camera"`, `"vintage camera"` |
| **lens** | Lens type | `"macro lens"`, `"wide angle"`, `"telephoto"` |
| **iso** | ISO setting | `"ISO 100"`, `"ISO 800"`, `"ISO 3200"` |
| **composition** | Composition style | `"rule of thirds"`, `"leading lines"`, `"symmetry"` |
| **mood** | Overall mood or atmosphere | `"dramatic"`, `"peaceful"`, `"energetic"` |
| **color_palette** | Color scheme | `"warm tones"`, `"monochrome"`, `"vibrant colors"` |
| **weather** | Weather conditions | `"sunny"`, `"stormy"`, `"foggy"` |
| **time_of_day** | Time setting | `"dawn"`, `"midday"`, `"dusk"`, `"midnight"` |
| **location** | Location or setting | `"urban street"`, `"mountain peak"`, `"studio"` |

### Usage Examples

**Using discrete parameters:**
```
/image-json subject:"a red sports car driving down the road" lighting:"street lights at night" focal_length:"85mm" aperture:"f/1.4" shutter_speed:"1/1000" style:"sports photography"
```

**Using raw JSON:**
```
/image-json json_string:{"subject":"a red sports car driving down the road","lighting":"street lights at night","focalLength":"85mm","aperture":"f/1.4","shutterSpeed":"1/1000","style":"sports photography"}
```

**Combining JSON with discrete parameters (discrete parameters override JSON):**
```
/image-json json_string:{"filter":"prism","mood":"dramatic"} filter_type:"red moon" style:"cinematic"
```
In this example, `filter_type` will be "red moon" (not "prism") and `style` will be "cinematic", while `mood` remains "dramatic".

All of these approaches convert your parameters into a JSON structure that serves as the prompt for image generation, giving the AI very specific technical guidance for creating your image. **The final JSON structure is displayed in the bot's response** so you can see exactly what was sent to the image generation API.

### Features

- **🆕 Raw JSON Support**: Pass structured JSON directly with the `json_string` parameter for maximum flexibility
- **🔀 Smart Parameter Merging**: Combine JSON with discrete parameters - discrete parameters always override JSON conflicts  
- **📋 40+ Parameters**: Comprehensive control over technical camera settings, creative aspects, and environmental conditions
- **🔍 Dropdown Choices**: Many parameters include predefined choices for common photography settings
- **⚡ Flexible Usage**: All parameters are optional - use as many or as few as needed
- **📸 Technical Precision**: Perfect for photographers who want specific camera settings simulated
- **🎨 Creative Control**: Combine technical and artistic parameters for unique results
- **✅ JSON Validation**: Clear error messages for invalid JSON with helpful examples
- **📱 Response Display**: Final JSON structure is shown in the bot's response for transparency

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
| `/daily-game stats` | Show participation statistics for a daily game | None |

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

**View participation statistics:**
```
/daily-game stats name:Wordle
/daily-game stats name:"My Game" start_date:"2024-01-01T00:00:00Z" end_date:"2024-01-31T23:59:59Z"
```

The stats command shows:
- Overall participation rates for each player (e.g., "played 25/30 days (83%)")
- Day-by-day breakdown showing which users participated each day
- Defaults to the last 30 days if no date range is specified
- Accepts UTC timestamps or ISO format dates for custom ranges

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
- **Participation statistics**: Track and analyze player participation over time with flexible date ranges

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

Create a new Cog under *bot/app/commands/*.  Register your command with `@app_commands.command` and add the cog in its own `setup` coroutine.

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

Structured JSON logs are written to *logs/cunningbot-YYYY-MM-DD.json* (date-rotated).  Adjust verbosity or format by editing *bot/app/logger.py*.

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
