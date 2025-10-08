# Testing Plan for CunningBot Discord Application

## Overview
This document outlines the comprehensive testing strategy for CunningBot, a Discord bot with multiple slash commands, task queue management, and integrations with external APIs (OpenAI, Google Gemini, Baseball API).

## Testing Levels

### 1. Integration Tests (Priority)
Integration tests should be the primary focus for slash commands, as they test the entire flow from Discord interaction to final response.

### 2. Unit Tests (Critical Functionality)
Unit tests should focus on business logic, data processing, and utility functions that can be tested in isolation.

---

## Slash Command Integration Tests

### `/chat` Command
**Location:** `bot/app/commands/chat/chat.py`

**Test Scenarios:**

1. **Basic Chat Request**
   - Send a simple message with default model
   - Verify response is received
   - Verify message is formatted correctly with user mention
   - Verify ephemeral settings work correctly

2. **Model Selection**
   - Test each supported model (gpt-3.5-turbo, gpt-4o-mini, gpt-4.1-nano, etc.)
   - Verify model metadata appears in response
   - Verify default model is used when not specified

3. **Message History**
   - Test with varying message_count values (0, 1, 20, 100)
   - Verify history is correctly retrieved and ordered (oldest first)
   - Verify bot and user messages are correctly differentiated
   - Verify message content is properly flattened

4. **Persona Handling**
   - Test with each available persona (discord_user, cat, helpful_assistant, sarcastic_jerk, homer_simpson)
   - Test with default persona setting
   - Verify persona instructions are applied to system prompt
   - Test fallback behavior when persona is not found

5. **Private/Public Response**
   - Test private=0 (public response)
   - Test private=1 (ephemeral response)
   - Verify followup messages respect ephemeral setting

6. **Queue Integration**
   - Test behavior when queue is empty (immediate processing)
   - Test behavior when queue has items (queue notification message)
   - Test queue full scenario (10+ tasks)
   - Verify already_responded flag is handled correctly

7. **Long Responses**
   - Test responses > 2000 characters
   - Verify message splitting works correctly
   - Verify all chunks are delivered

8. **Error Handling**
   - Test with invalid channel type (non-TextChannel)
   - Test with expired interaction
   - Test LLM API failure
   - Test network timeout
   - Verify error messages are ephemeral

### `/image` Command
**Location:** `bot/app/commands/image/image.py`

**Test Scenarios:**

1. **Basic Image Generation**
   - Generate image with simple prompt
   - Verify image is returned as Discord attachment
   - Verify prompt appears in response message
   - Verify default parameters (size=auto, quality=auto, background=auto, model=openai)

2. **Image Editing**
   - Upload an image and provide edit prompt
   - Verify edited image is returned
   - Verify filename includes "edited_" prefix
   - Test with various image formats (PNG, JPG, GIF)

3. **Size Options**
   - Test each size option (auto, 1024x1024, 1536x1024, 1024x1536)
   - Verify size metadata appears in response

4. **Quality Options (OpenAI only)**
   - Test each quality option (auto, high, medium, low)
   - Verify quality metadata appears in response

5. **Background Options (OpenAI only)**
   - Test each background option (auto, transparent, opaque)
   - Verify background metadata appears in response

6. **Model Selection**
   - Test OpenAI model
   - Test Google Gemini model
   - Test Gemini when GOOGLE_API_KEY is not configured

7. **Queue Integration**
   - Test behavior when queue is empty
   - Test behavior when queue has items
   - Test queue full scenario

8. **File Saving**
   - Verify images are saved to correct directory (generated_images/ or edited_images/)
   - Test behavior when file save fails (permission error)
   - Verify warning message appears when save fails
   - Verify image is still sent to Discord even if save fails

9. **Donation Message**
   - Test that donation message appears randomly (~5% of time)
   - Verify donation message is ephemeral

10. **Feature Toggles**
    - Test IMAGE_GENERATION_ENABLED=False
    - Test user in IMAGE_GENERATION_DISABLED_FOR_USERS list
    - Verify proper error messages

11. **Error Handling**
    - Test with invalid image attachment (corrupted file)
    - Test with oversized image
    - Test API failure (OpenAI or Gemini)
    - Test network timeout
    - Verify error messages include prompt and attachment info

### `/roll` Command
**Location:** `bot/app/commands/dice/roll.py`

**Test Scenarios:**

1. **Basic Dice Rolls**
   - Test default (no parameter) - should roll 1d20
   - Test simple roll: "d20", "1d20", "d6", "4d6"
   - Verify results are within valid range (1 to sides)
   - Verify result format includes roll breakdown

2. **Complex Expressions**
   - Test multiple dice: "2d6+3d4"
   - Test with arithmetic: "1d20+5", "2d6-3", "1d8*2", "4d6/2"
   - Test with parentheses: "(2d6+3)*2"
   - Verify expression evaluation is correct

3. **Edge Cases**
   - Test maximum dice count (100 dice)
   - Test maximum sides (1000 sides)
   - Test invalid expressions (no dice notation)
   - Test invalid dice counts (0d6, -1d6, 101d6)
   - Test invalid sides (d0, d-5, d1001)

4. **Response Formatting**
   - Single die: verify shows "d20: X"
   - Multiple dice: verify shows "[X, Y, Z] = Sum"
   - Verify user mention appears
   - Verify bold formatting for totals

5. **Error Handling**
   - Test empty expression
   - Test malformed expressions
   - Test unsafe expressions (no code injection)
   - Verify error messages are ephemeral

### `/queue` Command
**Location:** `bot/app/commands/queue.py`

**Test Scenarios:**

1. **Queue Status Display**
   - Test with empty queue
   - Test with queued tasks
   - Test with active tasks
   - Test with worker running
   - Test with worker stopped

2. **Status Information**
   - Verify queue_size is accurate
   - Verify active_tasks count is accurate
   - Verify completed_tasks count is accurate
   - Verify worker status indicator (🟢 Running / 🔴 Stopped)

3. **Ephemeral Response**
   - Verify response is always ephemeral

4. **Error Handling**
   - Test when task_queue is unavailable
   - Verify error message is ephemeral

### `/baseball agent` Command
**Location:** `bot/app/commands/baseball/agent.py`

**Test Scenarios:**

1. **Basic Agent Query**
   - Send simple baseball query (e.g., "Who won the World Series in 2023?")
   - Verify response is received
   - Verify response includes user message in formatted output

2. **API Integration**
   - Test with query requiring live API data
   - Verify agent calls appropriate API endpoints
   - Test behavior when API is unavailable

3. **Error Handling**
   - Test with invalid prompt
   - Test with API timeout
   - Test with API rate limiting

### `/daily-game` Command Group
**Location:** `bot/app/commands/daily_game/daily_game.py`

#### `/daily-game register`

**Test Scenarios:**

1. **Basic Registration**
   - Register new game with valid name, link, hour, minute
   - Verify game is saved to app state
   - Verify confirmation message includes channel mention and formatted time

2. **Validation**
   - Test with invalid URL (no http/https)
   - Test with invalid minute (not in [0, 10, 20, 30, 40, 50])
   - Test with invalid hour (< 0 or > 23)

3. **Uniqueness**
   - Try to register same game name in different channel
   - Verify error message prevents duplicate names across channels

4. **Update Existing Game**
   - Register game, then register again with different settings
   - Verify game is updated (not duplicated)

5. **Permissions**
   - Test without administrator permission
   - Verify command is rejected

#### `/daily-game enable`

**Test Scenarios:**

1. **Enable Disabled Game**
   - Disable a game, then enable it
   - Verify enabled flag is set to True
   - Verify confirmation message

2. **Enable Non-existent Game**
   - Try to enable game that doesn't exist
   - Verify error message

3. **Permissions**
   - Test without administrator permission

#### `/daily-game disable`

**Test Scenarios:**

1. **Disable Enabled Game**
   - Enable a game, then disable it
   - Verify enabled flag is set to False
   - Verify confirmation message

2. **Disable Non-existent Game**
   - Try to disable game that doesn't exist
   - Verify error message

3. **Permissions**
   - Test without administrator permission

#### `/daily-game delete`

**Test Scenarios:**

1. **Delete Existing Game**
   - Register a game, then delete it
   - Verify game is removed from app state
   - Verify confirmation message includes channel mention

2. **Delete Non-existent Game**
   - Try to delete game that doesn't exist
   - Verify error message

3. **Permissions**
   - Test without administrator permission

#### `/daily-game list`

**Test Scenarios:**

1. **List All Games**
   - Register multiple games (enabled and disabled)
   - Verify all games appear in list
   - Verify status indicators (✅ / 🚫)
   - Verify channel mentions are correct
   - Verify times are formatted correctly (HH:MM)

2. **List When No Games**
   - Call list when no games registered
   - Verify appropriate message

3. **Embed Formatting**
   - Verify embed color (green if any enabled, red if all disabled)
   - Verify footer shows total game count

#### `/daily-game preview`

**Test Scenarios:**

1. **Preview Registered Game**
   - Register a game, then preview it
   - Verify message format matches actual poster format
   - Verify game details are shown (status, channel, time)

2. **Preview Non-existent Game**
   - Try to preview game that doesn't exist
   - Verify error message

#### `/daily-game stats`

**Test Scenarios:**

1. **Basic Stats Query**
   - Generate stats for a game with participation data
   - Verify user participation counts are correct
   - Verify percentages are calculated correctly
   - Verify daily breakdown is shown (most recent first)

2. **Date Range**
   - Test with default date range (30 days)
   - Test with custom start_date
   - Test with custom end_date
   - Test with both start_date and end_date

3. **Date Parsing**
   - Test with Unix timestamp
   - Test with ISO format
   - Test with invalid format

4. **Date Validation**
   - Test with start_date >= end_date
   - Test with date range > 365 days
   - Test with future start_date
   - Test with future end_date

5. **Channel Validation**
   - Test in correct channel (where game is registered)
   - Test in wrong channel
   - Verify error message includes correct channel mention

6. **No Participation Data**
   - Test game with no participants
   - Verify appropriate message

7. **Long Response Handling**
   - Test game with many participants over long date range
   - Verify response is split into multiple messages if > 2000 chars

8. **Error Handling**
   - Test with non-TextChannel
   - Test with invalid game name
   - Test with stats analysis failure

### `/persona` Command Group
**Location:** `bot/app/commands/persona/default.py`

#### `/persona default`

**Test Scenarios:**

1. **View Current Default**
   - Call command without parameter
   - Verify current persona is displayed
   - Verify persona description is shown

2. **Set Default Persona**
   - Set each available persona as default
   - Verify confirmation message
   - Verify persona description is shown
   - Verify subsequent /chat commands use the new default

3. **Guild Configuration**
   - Test in configured guild
   - Test in unconfigured guild
   - Verify appropriate error messages for unconfigured guilds

4. **Invalid Persona**
   - Try to set persona not in CHAT_PERSONAS
   - Verify error message

5. **Error Handling**
   - Test with app state retrieval failure
   - Test with app state set failure

#### `/persona show`

**Test Scenarios:**

1. **List All Personas**
   - Verify all personas from CHAT_PERSONAS are shown
   - Verify names, keys, and descriptions are displayed
   - Verify current default is highlighted

2. **Long Descriptions**
   - Verify descriptions > 100 chars are truncated

3. **Guild Configuration**
   - Test in configured guild
   - Test in unconfigured guild
   - Verify current status message handles both cases

4. **Embed Formatting**
   - Verify footer includes usage instructions

### `/poll` Command
**Location:** `bot/app/commands/event/poll.py`

**Test Scenarios:**

1. **Basic Poll Creation**
   - Create poll with 2-10 options (semicolon-separated)
   - Verify poll embed is created with title
   - Verify all options are listed with emoji numbers
   - Verify reaction emojis are added (0️⃣ through 9️⃣)

2. **Poll Validation**
   - Test with 0 options (empty string)
   - Test with 1 option
   - Test with > 10 options
   - Verify appropriate error messages

3. **Poll Voting**
   - React with valid emoji (within options range)
   - Verify vote is recorded in active_polls
   - Verify poll message is updated with vote counts
   - Verify footer shows total votes

4. **Multiple Votes**
   - Same user votes for multiple options
   - Verify all votes are recorded
   - Verify vote counts update correctly

5. **Vote Removal**
   - User removes reaction
   - Verify vote is removed from active_polls
   - Verify poll message updates correctly

6. **Invalid Reactions**
   - React with emoji not in NUMBER_EMOJIS
   - React with emoji beyond option count (e.g., 9️⃣ on 3-option poll)
   - Verify these reactions are ignored

7. **Bot Reactions**
   - Verify bot's own reactions are ignored

8. **Poll Results**
   - Create poll, add votes, then call `/poll-results`
   - Verify results show correct vote counts
   - Verify voter names are displayed (up to 5, then "and X more")
   - Verify total votes count

9. **Poll Results by Message ID**
   - Call `/poll-results` with specific message_id
   - Verify correct poll results are shown

10. **Poll Results Without Message ID**
    - Call `/poll-results` without message_id
    - Verify most recent poll in channel is used

11. **Poll Results - No Active Polls**
    - Call `/poll-results` when no polls exist in channel
    - Verify error message

12. **Embed Updates**
    - Verify poll embed updates in real-time as votes come in
    - Verify footer includes creator name and total votes

13. **Error Handling**
    - Test poll creation failure
    - Test poll results failure
    - Test message edit failure during vote updates
    - Verify all error messages are ephemeral

---

## Unit Tests (Critical Functionality)

### Task Queue System
**Location:** `bot/app/task_queue.py`

**Test Scenarios:**

1. **Task Enqueueing**
   - Test enqueue_task with various handlers
   - Verify task_id generation is unique
   - Verify task is added to active_tasks
   - Verify queue size increases
   - Test queue full scenario (maxsize=10)

2. **Task ID Generation**
   - Test generate_task_id with interaction
   - Test generate_task_id without interaction
   - Verify IDs are unique across multiple calls

3. **Worker Lifecycle**
   - Test start_worker (worker starts)
   - Test start_worker when already running (no duplicate workers)
   - Test stop_worker (worker stops gracefully)
   - Test stop_worker when not running

4. **Task Processing**
   - Test processing async handler
   - Test processing sync handler
   - Test processing with args and kwargs
   - Verify task status transitions (QUEUED → PROCESSING → COMPLETED)

5. **Task Completion**
   - Verify completed task moves to completed_tasks
   - Verify completed task removed from active_tasks
   - Test completed task history limit (max 50)

6. **Task Failure**
   - Test handler that raises exception
   - Verify task status = FAILED
   - Verify error message is captured
   - Verify error is sent to user (if Discord interaction)

7. **Interaction Expiration**
   - Test task with expired interaction
   - Verify task status = CANCELLED
   - Verify task is not executed

8. **Queue Status**
   - Test get_queue_status returns accurate data
   - Test get_task_status for active task
   - Test get_task_status for completed task
   - Test get_task_status for non-existent task

9. **Concurrent Safety**
   - Test multiple tasks enqueued rapidly
   - Verify tasks are processed sequentially (one at a time)

### Dice Roller
**Location:** `bot/app/commands/dice/roll.py` (class `DiceRoller`)

**Test Scenarios:**

1. **roll_die**
   - Test various sides (1, 6, 20, 100)
   - Verify results are within range [1, sides]
   - Test with invalid sides (0, -1)

2. **roll_dice**
   - Test various count and sides combinations
   - Verify individual rolls and sum are correct
   - Test edge cases (count=1, count=100, sides=1, sides=1000)
   - Test invalid count (0, -1, 101)
   - Test invalid sides (0, -1, 1001)

3. **parse_and_roll**
   - Test simple rolls: "d20", "1d20", "4d6"
   - Test multiple dice: "2d6+3d4"
   - Test arithmetic: "1d20+5", "2d6-3", "1d8*2"
   - Test complex: "(2d6+3)*2", "1d20+3d6-2"
   - Test empty string (should default to "1d20")
   - Test invalid expressions (no dice notation)
   - Test unsafe expressions (code injection attempts)

4. **Dice Pattern Matching**
   - Test shorthand: "d20" → "1d20"
   - Test case insensitivity: "D20", "d20", "1D6"

5. **Breakdown Formatting**
   - Single die: verify format "dX: Y"
   - Multiple dice: verify format "XdY: [A, B, C] = Sum"

### Chat Service
**Location:** `bot/domain/chat/chat_service.py`

**Test Scenarios:**

1. **Message Building**
   - Test with msg only (minimal parameters)
   - Test with all parameters (model, name, personality, history)
   - Verify system prompt includes personality when provided
   - Verify history is included in messages
   - Verify user message is appended with sanitized name

2. **Model Selection**
   - Test with each supported model
   - Test with default model (None → gpt-4o-mini)

3. **Name Sanitization**
   - Test with default name (None → "User")
   - Test with name requiring sanitization (special characters)

4. **Personality Handling**
   - Test with personality string
   - Test without personality (None)
   - Verify system prompt format

5. **LLM Client Factory**
   - Verify ChatCompletionsClient.factory is called with correct model
   - Test error handling when factory fails

6. **Error Handling**
   - Test with LLM API failure
   - Verify exception is logged
   - Verify exception is re-raised

### Image Generation Client
**Location:** `bot/api/openai/image_generation_client.py`

**Test Scenarios:**

1. **Initialization**
   - Test with valid OPENAI_API_KEY
   - Test without OPENAI_API_KEY (should raise EnvironmentError)

2. **generate_image**
   - Test with simple prompt
   - Test with various sizes (1024x1024, 1536x1024, 1024x1536)
   - Test with various n values (1, 2, 3)
   - Verify returns (bytes, "") on success
   - Verify returns (None, error_msg) on failure

3. **Base64 Decoding**
   - Test with valid b64_json response
   - Test with missing b64_json
   - Test with invalid b64_json

4. **Error Handling**
   - Test with API failure (network error)
   - Test with API rate limit
   - Test with invalid API key
   - Verify error messages are captured and returned

5. **Factory Method**
   - Test factory creates client with specified model
   - Test factory with default model

### Daily Game Stats Service
**Location:** `bot/domain/daily_game/daily_game_stats_service.py`

**Test Scenarios:**

1. **get_default_date_range**
   - Verify returns last 30 days
   - Verify start_date is at midnight (00:00:00)
   - Verify end_date is current time

2. **parse_utc_timestamp**
   - Test with Unix timestamp string
   - Test with ISO format string
   - Test with ISO format with 'Z'
   - Test with invalid format
   - Verify timezone is always UTC

3. **validate_date_range**
   - Test valid range
   - Test start_date >= end_date (should raise ValueError)
   - Test range > 365 days (should raise ValueError)
   - Test future start_date (should raise ValueError)
   - Test future end_date (should raise ValueError)

4. **format_stats_response**
   - Test with valid GameStatsResult
   - Test with no participation data
   - Verify user mentions are formatted correctly
   - Verify participation counts and percentages are correct
   - Verify dates are formatted correctly (MM/DD/YYYY)
   - Verify daily breakdown is in reverse chronological order

5. **User Lookup**
   - Test with valid user IDs (user exists in bot)
   - Test with invalid user IDs (user not found)
   - Verify fallback to "<@USER_ID>" format

### OpenAI Utilities
**Location:** `bot/api/openai/utils.py` (assumed)

**Test Scenarios:**

1. **sanitize_name**
   - Test with valid name
   - Test with special characters (should remove/replace)
   - Test with empty string
   - Test with very long name (should truncate)

### Discord Utilities
**Location:** `bot/api/discord/utils.py` (assumed)

**Test Scenarios:**

1. **flatten_discord_message**
   - Test with simple text message
   - Test with message containing mentions
   - Test with message containing embeds
   - Test with message containing attachments

2. **format_response_with_interaction_user_message**
   - Test formatting with user mention
   - Test with original message

3. **to_tiny_text**
   - Test conversion to tiny text format

### File Service
**Location:** `bot/api/os/file_service.py` (assumed)

**Test Scenarios:**

1. **write_bytes**
   - Test writing to valid path
   - Test creating parent directories
   - Test with permission error
   - Test with disk full error
   - Test overwriting existing file

2. **read_bytes**
   - Test reading existing file
   - Test reading non-existent file
   - Test reading with permission error

### Logger
**Location:** `bot/app/utils/logger.py`

**Test Scenarios:**

1. **get_logger**
   - Verify returns logger instance
   - Verify logger name is set correctly

2. **Log Formatting**
   - Test info, warning, error, debug levels
   - Verify log output format
   - Test with dictionary payloads (JSON logging)

### App State Management
**Location:** `bot/app/app_state.py` (assumed)

**Test Scenarios:**

1. **get_default_persona**
   - Test with configured guild
   - Test with unconfigured guild
   - Test with invalid guild_id

2. **set_default_persona**
   - Test setting valid persona
   - Test setting invalid persona
   - Test with unconfigured guild

3. **get_state_value_from_interaction**
   - Test retrieving existing state value
   - Test retrieving non-existent state value

4. **set_state_value_from_interaction**
   - Test setting new state value
   - Test updating existing state value
   - Test with nested dictionary values

---

## Testing Infrastructure

### Test Framework
- **Primary Framework:** `pytest`
- **Async Support:** `pytest-asyncio`
- **Mocking:** `pytest-mock` or `unittest.mock`
- **Discord Mocking:** Create custom fixtures for discord.Interaction, discord.Message, etc.

### Test Organization
```
tests/
├── integration/
│   ├── commands/
│   │   ├── test_chat_command.py
│   │   ├── test_image_command.py
│   │   ├── test_roll_command.py
│   │   ├── test_queue_command.py
│   │   ├── test_baseball_command.py
│   │   ├── test_daily_game_commands.py
│   │   ├── test_persona_commands.py
│   │   └── test_poll_commands.py
│   └── test_task_queue_integration.py
├── unit/
│   ├── test_task_queue.py
│   ├── test_dice_roller.py
│   ├── test_chat_service.py
│   ├── test_image_generation_client.py
│   ├── test_daily_game_stats_service.py
│   ├── test_openai_utils.py
│   ├── test_discord_utils.py
│   ├── test_file_service.py
│   ├── test_logger.py
│   └── test_app_state.py
├── fixtures/
│   ├── discord_fixtures.py  # Mock interactions, messages, users, guilds
│   ├── llm_fixtures.py      # Mock LLM responses
│   └── api_fixtures.py      # Mock API responses
└── conftest.py              # Shared pytest configuration
```

### Mock Requirements

1. **Discord Objects**
   - discord.Interaction (with response, followup, user, channel, guild)
   - discord.Message
   - discord.User
   - discord.Guild
   - discord.TextChannel
   - discord.Embed
   - discord.Attachment

2. **External APIs**
   - OpenAI API (chat completions, image generation, image editing)
   - Google Gemini API
   - Baseball API

3. **File System**
   - Mock file operations to avoid actual disk writes

4. **Environment Variables**
   - Mock OPENAI_API_KEY, GOOGLE_API_KEY, etc.

### Test Execution Strategy

1. **Local Development**
   ```bash
   pytest tests/unit/           # Fast unit tests
   pytest tests/integration/    # Slower integration tests
   pytest                       # All tests
   ```

2. **CI/CD Pipeline**
   - Run unit tests on every commit
   - Run integration tests on every pull request
   - Generate coverage reports (aim for 80%+ coverage)
   - Block merges if tests fail

3. **Test Markers**
   ```python
   @pytest.mark.unit
   @pytest.mark.integration
   @pytest.mark.slow
   @pytest.mark.external_api  # Requires actual API access
   ```

---

## Coverage Goals

- **Critical Functionality (Unit Tests):** 90%+ coverage
  - Task queue system
  - Dice roller
  - Chat service
  - Stats service
  - Utilities

- **Slash Commands (Integration Tests):** 80%+ coverage
  - All happy path scenarios
  - Major error scenarios
  - Edge cases

- **Overall Project:** 75%+ coverage

---

## Testing Best Practices

1. **Isolation:** Each test should be independent and not rely on other tests
2. **Mocking:** Mock external dependencies (APIs, file system, Discord API)
3. **Fixtures:** Use pytest fixtures for common setup (mock interactions, users, etc.)
4. **Assertions:** Use descriptive assertion messages
5. **Test Naming:** Use descriptive names that explain what is being tested
6. **Documentation:** Each test module should have a docstring explaining its purpose
7. **Cleanup:** Ensure tests clean up after themselves (temp files, state changes)
8. **Parameterization:** Use pytest.mark.parametrize for testing multiple inputs
9. **Error Testing:** Always test error paths, not just happy paths
10. **Async Testing:** Use pytest-asyncio for async functions

---

## Test Maintenance

1. **Update Tests with Code Changes:** When slash commands are modified, update corresponding tests
2. **Add Tests for New Features:** Every new slash command or feature must include tests
3. **Review Test Failures:** Investigate and fix test failures immediately
4. **Refactor Tests:** Keep test code clean and maintainable
5. **Monitor Coverage:** Track coverage trends and address declining coverage

---

## Future Enhancements

1. **Performance Tests:** Measure response times for slash commands under load
2. **Load Tests:** Test task queue with high concurrency
3. **End-to-End Tests:** Full bot deployment tests in test Discord server
4. **Contract Tests:** Verify OpenAI/Gemini API contracts
5. **Mutation Testing:** Use mutation testing to verify test quality
6. **Visual Regression Tests:** For image generation features
7. **Chaos Testing:** Test resilience to API failures, network issues, etc.

---

## Appendix: Slash Command Summary

| Command | Location | Description |
|---------|----------|-------------|
| `/chat` | `bot/app/commands/chat/chat.py` | Chat with LLM (supports multiple models and personas) |
| `/image` | `bot/app/commands/image/image.py` | Generate or edit images (OpenAI/Gemini) |
| `/roll` | `bot/app/commands/dice/roll.py` | Roll dice with complex expressions |
| `/queue` | `bot/app/commands/queue.py` | Check task queue status |
| `/baseball agent` | `bot/app/commands/baseball/agent.py` | Baseball API queries via agent |
| `/daily-game register` | `bot/app/commands/daily_game/daily_game.py` | Register daily game reminder |
| `/daily-game enable` | `bot/app/commands/daily_game/daily_game.py` | Enable daily game |
| `/daily-game disable` | `bot/app/commands/daily_game/daily_game.py` | Disable daily game |
| `/daily-game delete` | `bot/app/commands/daily_game/daily_game.py` | Delete daily game |
| `/daily-game list` | `bot/app/commands/daily_game/daily_game.py` | List all daily games |
| `/daily-game preview` | `bot/app/commands/daily_game/daily_game.py` | Preview daily game message |
| `/daily-game stats` | `bot/app/commands/daily_game/daily_game.py` | Show participation stats |
| `/persona default` | `bot/app/commands/persona/default.py` | Set/view default persona |
| `/persona show` | `bot/app/commands/persona/default.py` | Show all available personas |
| `/poll` | `bot/app/commands/event/poll.py` | Create poll with emoji voting |
| `/poll-results` | `bot/app/commands/event/poll.py` | Show poll results |
