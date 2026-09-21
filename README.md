# CunningBot

CunningBot is a Discord bot for a few friends' servers. It chats, generates and
edits images, runs trivia, posts news and weather, and has a channel agent you
can talk to in plain English — one that can search the web, look up the
server's news, and publish web pages. It uses OpenAI and Google Gemini, keeps
its state in Redis, and runs under Docker Compose on a Raspberry Pi.

`/help` in Discord is the full, up-to-date command reference.

---

## Slash commands

| Command | What it does |
|---|---|
| `/chat` | Chat with an LLM. Pick the model, persona, context size, and whether the reply is private. |
| `/image` | Generate an image, or edit one you attach. Gemini and OpenAI image models. |
| `/image-json` | Generate an image from structured photography parameters (see below). |
| **Edit Images** (right-click a message → Apps) | Edit every image in a message with a prompt. |
| `/bot prompt:` | Ask the channel agent something once, in any channel — no setup needed. |
| `/agent` | Set up the agent in a channel: `register`, `configure`, `tool`, `status`, `pause`, `resume`, `unregister`. |
| `/schedule` | See and manage scheduled prompts: `list`, `cancel`, `pause`, `resume`. Create one by asking the agent ("post a summary here every day at 9am"). |
| `/trivia`, `/answer` | Scheduled trivia with difficulty-weighted scoring, weekly and all-time leaderboards, and personal stats. |
| `/news` | Add RSS feeds to a channel, posted directly or as AI summaries on a schedule, with filters and dedup. |
| `/weather` | On-demand forecasts and history by US ZIP code, or a daily forecast post. |
| `/framed` | Track daily [Framed](https://framed.wtf) results from a registered channel: leaderboards by points, player stats and streaks, daily rankings, head-to-head, a morning recap, and backfill from channel history. |
| `/poll`, `/poll-results` | Emoji-reaction polls with up to 10 options. |
| `/roll` | Dice: `1d20`, `4d6`, `3d6+2d4*10`. |
| `/af query` | Search Animation Factory GIFs with a private preview picker. |
| `/r` | Share a subreddit link: `/r python`. |
| `/persona` | Set or show the server's default chat persona. |
| `/bot-updates` | Choose channels that get restart notifications (admin). |
| `/queue` | Show the `/chat` and `/image` task queue. |
| `/help` | The full command reference. |

## The channel agent

Mention the bot, reply to it, or say its name — in any channel, including
threads — and it answers. It can use tools:

- weather, image generation and editing, GIFs, dice
- web search, reading other channels, searching the last week of this
  server's news summaries, and looking up Framed stats
- publishing a web page and finding, reading, or updating pages published
  earlier
- filing GitHub issues against this repo (off unless a channel enables it)

It can also end a reply with buttons for likely next messages, which anyone in
the channel can click instead of typing, and run a prompt on a schedule
("summarize this channel every weekday at 9am"), confirmed with buttons before
it's saved.

`/agent register` lets the bot also join a channel's conversation on its own,
with per-channel model, persona, response mode, cooldown, context window, and
tool settings. `/bot prompt:` runs the agent once anywhere without registering.

**Web pages** are served by a small Vercel app in `web/` — see `web/README.md`.
Pages published under a name live for a year and update in place; one-off
snapshots such as a day's chat summary get their own link and last 30 days.

## Background workers

Alongside the main bot, Docker Compose runs short-lived workers on a loop: RSS
feed posting, news summaries, breaking-news checks, trivia posting and closing,
the weekly trivia reset, scheduled weather posts, and reading each day's
Framed results. All of them share one
Redis instance with the bot.

## `/image-json`

Builds a JSON prompt from structured parameters and shows the final JSON in its
reply, so you can see exactly what was sent. Every parameter is optional.

| Parameter | Examples |
|---|---|
| `json_string` | Raw JSON to start from: `{"filter":"prism","mood":"dramatic"}` |
| `subject` | `"a red sports car driving down the road"` |
| `lighting` | `"street lights at night"`, `"golden hour"` |
| `style` | `"sports photography"`, `"cinematic"` |
| `focal_length`, `aperture`, `shutter_speed` | `"85mm"`, `"f/1.4"`, `"1/1000"` |
| `mood`, `color_palette`, `color_temperature` | `"dramatic"`, `"warm tones"`, `"5000k"` |
| `weather`, `time_of_day`, `location` | `"foggy"`, `"dusk"`, `"urban street"` |
| `custom_1`…`custom_3` with `custom_N_value` | Any extra key/value pair: `custom_1:"filterType" custom_1_value:"prism"` |
| `model`, `size`, `quality`, `background` | Image generation settings |

Discrete parameters override the same keys in `json_string`:

```
/image-json json_string:{"filter":"prism","mood":"dramatic"} style:"cinematic"
```

The command is at Discord's 25-option limit, so new keys go through the
`custom_*` pairs rather than new options.

## Personas

| Persona | Style |
|---|---|
| **A discord user** (`discord_user`) | Default — casual Discord chat |
| **Cat** (`cat`) | A literal cat |
| **Helpful Assistant** (`helpful_assistant`) | Professional and informative |
| **Sarcastic Jerk** (`sarcastic_jerk`) | Sarcasm and attitude |
| **Homer Simpson** (`homer_simpson`) | Homer Simpson |

`/chat` can override the persona per message, and agent channels set their own.
Only servers listed in `.guild_config.json` can change their default persona;
others use "A discord user".

## Project layout

```
├── bot/
│   ├── api/            # Clients for outside services (OpenAI, Google, Perplexity, Open-Meteo, GitHub, pages, …)
│   ├── domain/         # Feature logic, including the agent and its tools (no discord.py)
│   ├── app/
│   │   ├── commands/   # Slash commands, one folder per feature, auto-loaded
│   │   ├── tasks/      # Background workers
│   │   └── redis/      # One store per feature; all Redis access goes through these
│   └── main.py         # Entry point
├── web/                # Vercel app that hosts published pages
├── tests/              # pytest suite
├── scripts/            # One-off tools, e.g. check_models.py
├── docker-compose.yml  # The bot, the workers, and Redis
└── Makefile            # Docker shortcuts
```

## Setup

### 1. Create `.env`

```bash
cp .env.example .env
```

| Variable | Needed for |
|---|---|
| `DISCORD_TOKEN` | Required — the bot token |
| `OPENAI_API_KEY` | Required — chat, the agent, OpenAI images |
| `GOOGLE_API_KEY` | Gemini image models |
| `PERPLEXITY_API_KEY` | The agent's web search |
| `REDIS_HOST`, `REDIS_PORT`, `REDIS_DB`, `REDIS_PASSWORD` | Redis (Compose provides Redis itself) |
| `PAGES_BASE_URL`, `PAGES_PUBLISH_TOKEN`, `PAGES_ID_SECRET` | Publishing web pages |
| `GITHUB_TOKEN`, `GITHUB_ISSUE_REPO` | Filing GitHub issues from the agent |

Anything optional that's missing just turns that feature off.

**With Docker Compose, a variable must also be listed in that service's
`environment:` block** in `docker-compose.yml` — `.env` isn't copied into the
image, so a key that isn't listed never reaches the container.

### 2. Run it

```bash
make up      # docker compose up -d: the bot, all workers, and Redis
make logs    # follow logs
make down    # stop everything
make rebuild # rebuild images from scratch and restart
```

Code under `bot/domain` and `bot/api` is baked into the image, so changes there
need a rebuild; `bot/app` is mounted from the host.

To run just the bot without Docker you need Python 3.11 and a Redis server:

```bash
make install   # pip install -r requirements.txt
make run       # python3 -m bot.main
```

## Development

```bash
python3 -m pytest tests/
```

The suite runs in a few seconds and should be fully green. Some test modules
currently need `OPENAI_API_KEY` set to import.

- **Adding a command:** add a cog under `bot/app/commands/<feature>/` with an
  `async def setup(bot)`; it's loaded automatically. Then add it to `/help`
  (`HELP_PAGES` in `bot/app/commands/help.py`) — that's the bot's user-facing
  documentation.
- **Adding models:** edit `bot/domain/llm/models.py`, then run
  `python3 scripts/check_models.py` to confirm the model actually works.
- **Logs:** structured JSON lines in `logs/YYYY-MM-DD.jsonl`.

Contributor and AI-agent guidance lives in `AGENTS.md` and
`.claude/skills/cunningbot/` (architecture, how-to recipes, deploy, and the
backlog).

## Deployment

Merging to `main` does **not** deploy. On the Pi:

```bash
cd /home/dad/cunningbot && git pull && docker compose up -d --build
```

The pages app in `web/` deploys separately with `vercel --prod` from `web/`.
