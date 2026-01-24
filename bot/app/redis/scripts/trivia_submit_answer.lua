-- trivia_submit_answer.lua
-- Atomically validate game state and record trivia submission
--
-- This script prevents race conditions by executing entirely within Redis.
-- No other operation can interleave between checking the game state and recording the submission.
--
-- KEYS[1]: trivia:{guild_id}:games:active (hash containing all active games)
-- KEYS[2]: trivia:{guild_id}:game:{game_id}:submissions (hash for this game's submissions)
--
-- ARGV[1]: game_id
-- ARGV[2]: user_id
-- ARGV[3]: submission_data (JSON string with answer, submitted_at, is_correct, feedback, validated_at)
-- ARGV[4]: current_timestamp (epoch seconds as float)
--
-- Returns:
--   {ok = "SUBMITTED"} on success
--   {err = "GAME_NOT_FOUND"} if game doesn't exist
--   {err = "GAME_CLOSED"} if game has closed_at flag set
--   {err = "WINDOW_CLOSED"} if current time > ends_at

local games_hash_key = KEYS[1]
local submissions_key = KEYS[2]
local game_id = ARGV[1]
local user_id = ARGV[2]
local submission_data = ARGV[3]
local current_time = tonumber(ARGV[4])

-- Load game metadata from active games hash
local game_json = redis.call('HGET', games_hash_key, game_id)

if not game_json then
    return {err = "GAME_NOT_FOUND"}
end

-- Parse game data
local game = cjson.decode(game_json)

-- Check if game is already closed (closer has marked it)
if game.closed_at then
    return {err = "GAME_CLOSED"}
end

-- Check if answer window has passed
-- ends_at is ISO 8601 string, we need to convert to timestamp for comparison
-- Python will pass us ends_at as epoch timestamp in the submission call
local ends_at = game.ends_at_epoch

if ends_at and current_time > ends_at then
    return {err = "WINDOW_CLOSED"}
end

-- All checks passed - record submission atomically
-- HSET allows overwrite (user can update their answer)
redis.call('HSET', submissions_key, user_id, submission_data)

return {ok = "SUBMITTED"}
