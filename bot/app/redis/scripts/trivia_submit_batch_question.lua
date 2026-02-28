-- trivia_submit_batch_question.lua
-- Atomically submit a single question's answer within an active batch game.
--
-- Unlike trivia_submit_batch_answer.lua (which replaces the whole submission),
-- this script reads an existing per-user submission, checks if that specific
-- question was already answered, and merges the new answer in atomically.
--
-- KEYS[1]: trivia:{guild_id}:games:active
-- KEYS[2]: trivia:{guild_id}:game:{batch_id}:submissions
--
-- ARGV[1]: batch_id
-- ARGV[2]: user_id
-- ARGV[3]: question_num (string, e.g. "1", "2", "3")
-- ARGV[4]: answer_json (pre-encoded answer object with is_correct, points, etc.)
-- ARGV[5]: current_timestamp (epoch seconds as float)
--
-- Returns:
--   {"ok", "SUBMITTED"} on success
--   {"err", "GAME_NOT_FOUND"} if game doesn't exist
--   {"err", "GAME_CLOSED"} if game has closed_at flag set
--   {"err", "WINDOW_CLOSED"} if current time > ends_at
--   {"err", "ALREADY_SUBMITTED"} if user already answered this specific question

local games_hash_key = KEYS[1]
local submissions_key = KEYS[2]
local batch_id = ARGV[1]
local user_id = ARGV[2]
local question_num = ARGV[3]
local answer_json = ARGV[4]
local current_time = tonumber(ARGV[5])

-- Load game metadata from active games hash
local game_json = redis.call('HGET', games_hash_key, batch_id)

if not game_json then
    return {"err", "GAME_NOT_FOUND"}
end

local game = cjson.decode(game_json)

-- Check if game is already closed
if game.closed_at then
    return {"err", "GAME_CLOSED"}
end

-- Check if answer window has passed
local ends_at = game.ends_at_epoch
if ends_at and current_time > ends_at then
    return {"err", "WINDOW_CLOSED"}
end

-- Load existing submission for this user (or start fresh)
local existing_json = redis.call('HGET', submissions_key, user_id)
local submission

if existing_json then
    submission = cjson.decode(existing_json)
    if not submission.answers then
        submission.answers = {}
    end
else
    submission = {
        submitted_at = tostring(current_time),
        correct_count = 0,
        points = 0,
        total_count = 0
    }
    submission.answers = {}
end

-- Check if this specific question was already answered
-- We check both string key and integer key to handle cjson edge cases
if submission.answers[question_num] or submission.answers[tonumber(question_num)] then
    return {"err", "ALREADY_SUBMITTED"}
end

-- Record this question's answer
local answer_obj = cjson.decode(answer_json)
submission.answers[question_num] = answer_obj

-- Recompute aggregate stats from all answered questions
local correct_count = 0
local total_points = 0
local total_count = 0

for qnum, ans in pairs(submission.answers) do
    total_count = total_count + 1
    if ans.is_correct then
        correct_count = correct_count + 1
    end
    if ans.points then
        total_points = total_points + ans.points
    end
end

submission.correct_count = correct_count
submission.points = total_points
submission.total_count = total_count
submission.last_updated = tostring(current_time)

-- Write updated submission back atomically
redis.call('HSET', submissions_key, user_id, cjson.encode(submission))

return {"ok", "SUBMITTED"}
