# Trivia Custom Seed Words

## Overview

The trivia question generator now supports configurable seed words (topics and modifiers) per channel registration, with automatic fallback to the code-defined default lists when not configured.

## How It Works

### Seed Generation

Trivia questions are generated using a combination of:
- **Base Words (Topics)**: Subject matter words like "magic", "cyberpunk", "JRPG", etc.
- **Modifiers**: Context words like "origin", "impact", "controversy", "bug", etc.

These are combined to create unique seeds (e.g., "magic_origin", "JRPG_controversy") which guide the LLM to generate specific trivia questions.

### Default Behavior

If no custom seed words are configured for a registration:
- Uses the default `BASE_WORDS` list (~286 nerd culture topics)
- Uses the default `MODIFIERS` list (~95 contextual modifiers)
- Total possible combinations: ~27,000 unique questions

### Custom Configuration

Administrators can configure custom seed words:
- Per registration (scheduled trivia)
- Per ad-hoc post (immediate trivia)
- Can customize topics only, modifiers only, or both
- Minimum 2 words required for each list

## Discord Commands

### Register with Custom Seeds

```
/trivia register
  schedule: "8:00,17:00"
  answer_window: "1h"
  base_words: "python, javascript, rust, golang, typescript"
  modifiers: "origin, best_practice, pitfall, performance, debugging"
```

This creates a programming-focused trivia registration with only 5 topics and 5 modifiers (25 possible combinations).

### Configure Seeds for Existing Registration

```
/trivia configure_seeds
  registration: "abc123"
  base_words: "star wars, star trek, babylon 5, firefly"
  modifiers: "episode, character, ship, controversy, fan_theory"
```

Updates an existing registration to use sci-fi TV show topics.

### Post Immediate Trivia with Custom Seeds

```
/trivia post
  base_words: "redis, postgresql, mongodb, cassandra"
  modifiers: "use_case, limitation, performance, scaling"
```

Posts a single trivia question using database-focused seeds.

### Remove Custom Configuration

```
/trivia configure_seeds
  registration: "abc123"
```

Leave both `base_words` and `modifiers` empty to revert to defaults.

### View Configuration

```
/trivia list
```

Shows all registrations with their seed configuration:
- Default registrations: No seed info shown
- Custom registrations: Shows "Custom seeds: X topics, Y modifiers"

## Implementation Details

### Files Modified

1. **bot/domain/trivia/question_seeds.py**
   - Updated `generate_seed()` to accept optional word lists
   - Updated `get_unused_seed()` to accept optional word lists
   - Updated `get_total_possible_seeds()` to accept optional word lists

2. **bot/app/tasks/trivia_game_poster.py**
   - Extracts custom seed words from registration
   - Passes them to `get_unused_seed()`

3. **bot/app/commands/trivia/trivia.py**
   - Added optional `base_words` and `modifiers` parameters to `/trivia register`
   - Added optional `base_words` and `modifiers` parameters to `/trivia post`
   - Added new `/trivia configure_seeds` command
   - Updated `/trivia list` to show custom seed configuration

4. **bot/app/redis/trivia_store.py**
   - No changes needed (registration data is stored as JSON)

### Data Storage

Custom seed words are stored in the registration data:
```json
{
  "channel_id": 123456789,
  "schedule_times": ["8:00", "17:00"],
  "answer_window_minutes": 60,
  "enabled": true,
  "created_at": "2024-01-01T00:00:00Z",
  "base_words": ["topic1", "topic2", "topic3"],
  "modifiers": ["modifier1", "modifier2", "modifier3"]
}
```

If `base_words` or `modifiers` fields are absent, the system uses the defaults.

## Use Cases

### Themed Trivia Channels

Create specialized trivia for different communities:

**Gaming Channel:**
```
base_words: "NES, SNES, PlayStation, Xbox, Steam, speedrun, roguelike"
modifiers: "history, controversy, record, glitch, community"
```

**Tech Channel:**
```
base_words: "Linux, Docker, Kubernetes, Git, CI/CD, microservices"
modifiers: "best_practice, pitfall, evolution, controversy, debugging"
```

**Movie Channel:**
```
base_words: "Star Wars, MCU, LOTR, Pixar, Studio Ghibli"
modifiers: "behind_the_scenes, easter_egg, cut_content, controversy, fan_theory"
```

### Narrowed Question Pool

Reduce the question pool for more frequent repetition with variation:
- Default: ~27,000 combinations
- Custom (10 topics × 10 modifiers): 100 combinations
- Good for daily trivia that cycles through focused topics

### Educational Focus

Create learning-focused trivia for specific subjects:
```
base_words: "sorting_algorithm, hash_table, binary_tree, graph, dynamic_programming"
modifiers: "time_complexity, space_complexity, use_case, implementation, tradeoff"
```

## Testing

To test the implementation:

1. **Test default behavior:**
   ```
   /trivia post
   ```
   Should use default seed words.

2. **Test custom seeds in registration:**
   ```
   /trivia register schedule:"9:00" answer_window:"1h" base_words:"topic1, topic2" modifiers:"mod1, mod2"
   /trivia list
   ```
   Should show "Custom seeds: 2 topics, 2 modifiers"

3. **Test custom seeds in immediate post:**
   ```
   /trivia post base_words:"test1, test2, test3" modifiers:"context1, context2"
   ```
   Should generate question using custom seeds.

4. **Test configure_seeds:**
   ```
   /trivia configure_seeds registration:"abc" base_words:"new1, new2"
   /trivia list
   ```
   Should update the configuration.

5. **Test validation:**
   ```
   /trivia post base_words:"only_one"
   ```
   Should show error: "Base words must contain at least 2 words."

## Migration

No migration needed:
- Existing registrations without custom seeds continue working with defaults
- New optional fields are backwards compatible
- No changes to Redis schema
