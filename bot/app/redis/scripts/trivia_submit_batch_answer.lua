-- trivia_submit_batch_answer.lua
-- Atomically validate game state and record batch trivia submission
--
-- This script prevents race conditions by executing entirely within Redis.
-- No other operation can interleave between checking the game state and recording the submission.
--
-- KEYS[1]: trivia:{guild_id}:games:active (hash containing all active games)
-- KEYS[2]: trivia:{guild_id}:game:{batch_id}:submissions (hash for this batch game's submissions)
--
-- ARGV[1]: batch_id
-- ARGV[2]: user_id
-- ARGV[3]: submission_data (JSON string with answers dict, submitted_at, score)
-- ARGV[4]: current_timestamp (epoch seconds as float)
--
-- Returns:
--   {"ok", "SUBMITTED"} on success
--   {"err", "GAME_NOT_FOUND"} if game doesn't exist
--   {"err", "GAME_CLOSED"} if game has closed_at flag set
--   {"err", "WINDOW_CLOSED"} if current time > ends_at
--   {"err", "ALREADY_SUBMITTED"} if user has already submitted answers

local games_hash_key = KEYS[1]
local submissions_key = KEYS[2]
local batch_id = ARGV[1]
local user_id = ARGV[2]
local submission_data = ARGV[3]
local current_time = tonumber(ARGV[4])

-- Load game metadata from active games hash
local game_json = redis.call('HGET', games_hash_key, batch_id)

if not game_json then
    return {"err", "GAME_NOT_FOUND"}
end

-- Parse game data
local game = cjson.decode(game_json)

-- Check if game is already closed (closer has marked it)
if game.closed_at then
    return {"err", "GAME_CLOSED"}
end

-- Check if answer window has passed
local ends_at = game.ends_at_epoch

if ends_at and current_time > ends_at then
    return {"err", "WINDOW_CLOSED"}
end

-- Check if user has already submitted answers
local already_submitted = redis.call('HEXISTS', submissions_key, user_id)
if already_submitted == 1 then
    return {"err", "ALREADY_SUBMITTED"}
end

-- All checks passed - record submission atomically
redis.call('HSET', submissions_key, user_id, submission_data)

return {"ok", "SUBMITTED"}
