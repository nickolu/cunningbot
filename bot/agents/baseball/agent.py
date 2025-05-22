import json
from typing import List, Any
from agents import FunctionTool, Tool, RunContextWrapper
from bot.api.mlb.mlb_context_client import MlbClient
from bot.api.openai.agent_client import AgentClient
from bot.utils import logging_decorator
from datetime import datetime

from .context import team_id_context, league_id_context

class BaseBallAgentTools:
    def __init__(self) -> None:
        self.mlb_context_client = MlbClient()

    def __iter__(self) -> List[Tool]:
        return self.get_tools()

    def get_tools(self) -> List[Tool]:
        return [
            self.get_leagues(),
            self.get_standings(),
            self.get_schedule(),
            self.get_team_info_by_id(),
            self.get_player_info_by_id(),
            self.get_boxscore(),
            self.get_game_plays(),
            self.get_game_pace(),
            self.get_scoring_plays(),
            self.get_linescore(),
            self.get_latest_season(),
            self.lookup_player(),
            self.lookup_team(),
            self.get_teams(),
        ]

    @logging_decorator
    def get_leagues(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "sport_id": {"type": "integer"}
            },
            "required": ["sport_id"],
            "additionalProperties": False
        }

        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_leagues(sport_id=args["sport_id"])
        return FunctionTool(
            name="get_leagues_by_sport_id",
            description="Retrieves a list of baseball leagues based on a given sport ID. Useful for discovering available leagues.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_standings(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "league_id": {"type": "integer"},
                "season": {"type": "integer"}
            },
            "required": ["league_id", "season"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_standings(league_id=args["league_id"], season=args["season"])
        return FunctionTool(
            name="get_league_standings_by_season",
            description="Fetches the current standings for a specific league and season. Provides team rankings, wins, losses, and other relevant statistics.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_schedule(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "date": {"type": "string", "format": "date", "description": "YYYY-MM-DD", "nullable": True},
                "start_date": {"type": "string", "format": "date", "nullable": True},
                "end_date": {"type": "string", "format": "date", "nullable": True},
                "sport_id": {"type": "integer", "default": 1},
                "team_id": {"type": "integer", "nullable": True}
            },
            "required": ["date", "start_date", "end_date", "sport_id", "team_id"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_schedule(
                date=args.get("date"),
                start_date=args.get("start_date"),
                end_date=args.get("end_date"),
                sport_id=args.get("sport_id", 1),
                team_id=args.get("team_id")
            )
        return FunctionTool(
            name="get_game_schedule",
            description="Retrieves the game schedule. Can be filtered by a specific date, a date range, sport ID, or team ID. Essential for finding out when games are played.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_team_info_by_id(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "team_id": {"type": "integer"},
                "season": {"type": "integer", "nullable": True},
                "sport_id": {"type": "integer", "nullable": True},
                "hydrate": {"type": "string", "nullable": True},
                "fields": {"type": "string", "nullable": True}
            },
            "required": ["team_id", "season", "sport_id", "hydrate", "fields"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_team_info(
                team_id=args["team_id"],
                season=args.get("season"),
                sport_id=args.get("sport_id"),
                hydrate=args.get("hydrate"),
                fields=args.get("fields")
            )
        return FunctionTool(
            name="get_team_information_by_id",
            description="Provides detailed information about a specific baseball team using its unique ID. Optional parameters include season, sport ID, hydration options for related data, and specific fields to return.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_player_info_by_id(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "player_id": {"type": "integer"}
            },
            "required": ["player_id"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_player_info_by_id(player_id=args["player_id"])
        return FunctionTool(
            name="get_player_information_by_id",
            description="Fetches detailed information for a specific baseball player using their unique ID. Includes biographical data, career stats, and current team affiliation.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_boxscore(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "game_id": {"type": "integer"},
                "timecode": {"type": "string", "nullable": True},
                "fields": {"type": "string", "nullable": True}
            },
            "required": ["game_id", "timecode", "fields"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_boxscore(
                game_id=args["game_id"],
                timecode=args.get("timecode"),
                fields=args.get("fields")
            )
        return FunctionTool(
            name="get_game_boxscore_by_id",
            description="Retrieves the boxscore for a specific game using its unique ID. Includes detailed statistics for both teams and individual players, such as hits, runs, errors, and pitching/batting performance. Optional timecode and fields parameters allow for more specific data retrieval.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_game_plays(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "game_id": {"type": "integer"}
            },
            "required": ["game_id"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_game_plays(game_id=args["game_id"])
        return FunctionTool(
            name="get_game_play_by_play_by_id",
            description="Fetches the play-by-play data for a specific game using its unique ID. Provides a detailed account of every play that occurred during the game.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_game_pace(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "season": {"type": "integer"},
                "sport_id": {"type": "integer", "default": 1}
            },
            "required": ["season", "sport_id"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_game_pace(season=args["season"], sport_id=args.get("sport_id", 1))
        return FunctionTool(
            name="get_game_pace_stats_by_season",
            description="Retrieves game pace statistics for a given season and sport ID. Useful for analyzing the average length and flow of games.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_scoring_plays(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "game_id": {"type": "integer"},
                "eventType": {"type": "string", "nullable": True},
                "timecode": {"type": "string", "nullable": True},
                "fields": {"type": "string", "nullable": True}
            },
            "required": ["game_id", "eventType", "timecode", "fields"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_scoring_plays(
                game_id=args["game_id"],
                eventType=args.get("eventType"),
                timecode=args.get("timecode"),
                fields=args.get("fields")
            )
        return FunctionTool(
            name="get_game_scoring_plays_by_id",
            description="Fetches all scoring plays for a specific game using its unique ID. Can be filtered by event type, timecode, and specific fields to return.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_linescore(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "game_id": {"type": "integer"}
            },
            "required": ["game_id"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_linescore(game_id=args["game_id"])
        return FunctionTool(
            name="get_game_linescore_by_id",
            description="Retrieves the linescore for a specific game using its unique ID. Shows runs scored by each team per inning.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_latest_season(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "sport_id": {"type": "integer", "default": 1}
            },
            "required": ["sport_id"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_latest_season(sport_id=args.get("sport_id", 1))
        return FunctionTool(
            name="get_latest_sport_season",
            description="Determines the most recent or current season for a given sport ID.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def lookup_player(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "lookup_value": {"type": "string"},
                "game_type": {"type": "string", "default": "R"},
                "season": {"type": "integer", "nullable": True},
                "sport_id": {"type": "integer", "default": 1}
            },
            "required": ["lookup_value", "game_type", "season", "sport_id"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.lookup_player(
                lookup_value=args["lookup_value"],
                game_type=args.get("game_type", "R"),
                season=args.get("season"),
                sport_id=args.get("sport_id", 1)
            )
        return FunctionTool(
            name="lookup_player_by_name_or_id",
            description="Searches for a player using a lookup value (e.g., name or ID). Can be filtered by game type, season, and sport ID.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def lookup_team(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "lookup_value": {"type": "string"},
                "sport_id": {"type": "integer", "default": 1}
            },
            "required": ["lookup_value", "sport_id"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.lookup_team(
                lookup_value=args["lookup_value"],
                sport_id=args.get("sport_id", 1)
            )
        return FunctionTool(
            name="lookup_team_by_name_or_id",
            description="Searches for a team using a lookup value (e.g., name or ID). Can be filtered by sport ID.",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )

    @logging_decorator
    def get_teams(self) -> FunctionTool:
        schema = {
            "type": "object",
            "properties": {
                "sport_id": {"type": "integer", "default": 1},
                "season": {"type": "integer", "nullable": True},
                "active_status": {"type": "string", "nullable": True}
            },
            "required": ["sport_id", "season", "active_status"],
            "additionalProperties": False
        }
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            args = json.loads(args_json)
            return self.mlb_context_client.get_teams(
                sport_id=args.get("sport_id", 1),
                season=args.get("season"),
                active_status=args.get("active_status")
            )
        return FunctionTool(
            name="get_teams_by_sport_season_status",
            description="Retrieves a list of teams. Can be filtered by sport ID, season, and active status (e.g., 'Y' for active, 'N' for inactive).",
            params_json_schema=schema,
            on_invoke_tool=invoke
        )


class BaseballAgent:
    def __init__(self) -> None:
        self.tools = BaseBallAgentTools().get_tools()
        self.agent_client = AgentClient(
            name="Baseball Agent",
            instructions=f"You are a baseball agent who knows about baseball stats (our free api is limited to those date ranges). You can answer questions about baseball. The current date is {datetime.now().strftime('%Y-%m-%d')}. The current season is {datetime.now().strftime('%Y')}.\n\nTeam Ids:\n" + team_id_context + "\nLeague Ids:\n" + league_id_context,
            tools=list(self.tools)
        )
    
    async def run(self, prompt: str) -> str:
        return await self.agent_client.run(prompt)