import json
from typing import List, Optional, Any, Awaitable, Union
from agents import FileSearchTool, FunctionTool, Tool, WebSearchTool, ComputerTool, HostedMCPTool, LocalShellTool, ImageGenerationTool, CodeInterpreterTool, RunContextWrapper
from bot.agents.baseball_agent_context import team_id_context
from bot.api.api_sports.mlb_client import MlbClient
from bot.api.openai.agent_client import AgentClient
from pydantic import BaseModel, ConfigDict

head_to_head_games_function_schema = {
    "type": "object",
    "properties": {
        "team1id": {"type": "integer"},
        "team2id": {"type": "integer"},
        "date": {"type": "string"},
        "league": {"type": "integer"},
        "season": {"type": "integer"},
        "timezone": {"type": "string"}
    },
    "required": ["team1id", "team2id", "date", "league", "season", "timezone"],
    "additionalProperties": False
}

standings_function_schema = {
    "type": "object",
    "properties": {
        "league": {"type": "integer"},
        "season": {"type": "integer"},
        "team": {"type": "integer"},
        "stage": {"type": "string"},
        "group": {"type": "string"}
    },
    "required": ["league", "season", "team", "stage", "group"],
    "additionalProperties": False
}

games_function_schema = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "date": {"type": "string"},
        "league": {"type": "integer"},
        "season": {"type": "integer"},
        "team": {"type": "integer"},
        "timezone": {"type": "string"}
    },
    "required": ["id", "date", "league", "season", "team", "timezone"],
    "additionalProperties": False
}

player_statistics_function_schema = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "date": {"type": "string"},
        "league": {"type": "integer"},
        "season": {"type": "integer"},
        "team": {"type": "integer"},
        "timezone": {"type": "string"}
    },
    "required": ["id", "date", "league", "season", "team", "timezone"],
    "additionalProperties": False
}

timezones_function_schema = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False
}

leagues_function_schema = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "country_id": {"type": "integer"},
        "country": {"type": "string"},
        "type": {"type": "string"},
        "season": {"type": "integer"},
        "search": {"type": "string"}
    },
    "required": ["id", "name", "country_id", "country", "type", "season", "search"],
    "additionalProperties": False
}

countries_function_schema = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "code": {"type": "string"},
        "search": {"type": "string"}
    },
    "required": ["id", "name", "code", "search"],
    "additionalProperties": False
}

seasons_function_schema = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False
}

class BaseBallAgentTools:
    def __init__(self) -> None:
        self.baseball_client = BaseballClient()

    def __iter__(self) -> List[Tool]:
        return self.get_tools()

    def get_tools(self) -> List[Tool]:
        return [
            self.get_head_to_head_games(),
            self.get_standings(),
            self.get_games(),
            self.get_timezones(),
            self.get_leagues(),
            self.get_countries(),
            self.get_seasons()
        ]

    def get_leagues(self) -> FunctionTool:
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            print("get_leagues", args_json)
            args = json.loads(args_json)
            leagues = self.baseball_client.get_leagues(
                id=args["id"],
                name=args["name"],
                country_id=args["country_id"],
                country=args["country"],
                type=args["type"],
                season=args["season"],
                search=args["search"]
            )
            print("leagues", leagues)
            return leagues
        return FunctionTool(
            name="leagues",
            description="Get leagues for a league and season",
            params_json_schema=leagues_function_schema,
            on_invoke_tool=invoke
        )

    def get_countries(self) -> FunctionTool:
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            print("get_countries", args_json)
            args = json.loads(args_json)
            countries = self.baseball_client.get_countries(
                id=args["id"],
                name=args["name"],
                code=args["code"],
                search=args["search"]
            )
            print("countries", countries)
            return countries
        return FunctionTool(
            name="countries",
            description="Get valid countries",
            params_json_schema=countries_function_schema,
            on_invoke_tool=invoke
        )
    def get_seasons(self) -> FunctionTool:
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            print("get_seasons", args_json  )
            return self.baseball_client.get_seasons()
        return FunctionTool(
            name="seasons",
            description="Get available seasons",
            params_json_schema=seasons_function_schema,
            on_invoke_tool=invoke
        )
    def get_timezones(self) -> FunctionTool:
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            print("get_timezones", args_json)
            timezones = self.baseball_client.get_timezones()
            print("timezones", timezones)
            return timezones
        return FunctionTool(
            name="timezones",
            description="Get valid timezones",
            params_json_schema=timezones_function_schema,
            on_invoke_tool=invoke
        )
    
    def get_games(self) -> FunctionTool:
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            print("get_games", args_json)
            args = json.loads(args_json)
            games = self.baseball_client.get_games(
                id=args["id"],
                date=args["date"],
                league=args["league"],
                season=args["season"],
                timezone=args["timezone"]
            )
            print("games", games)
            return games
        return FunctionTool(
            name="games",
            description="Get games",
            params_json_schema=games_function_schema,
            on_invoke_tool=invoke
        )
    def get_standings(self) -> FunctionTool:
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            print("get_standings", args_json    )
            args = json.loads(args_json)
            standings = self.baseball_client.get_standings(
                league=args["league"],
                season=args["season"],
                team=args["team"],
                stage=args["stage"],
                group=args["group"]
            )
            print("standings", standings)
            return standings
        return FunctionTool(
            name="standings",
            description="Get standings",
            params_json_schema=standings_function_schema,
            on_invoke_tool=invoke
        )

    def get_head_to_head_games(self) -> FunctionTool:
        async def invoke(run_context: RunContextWrapper[Any], args_json: str) -> Any:
            print("get_head_to_head_games", args_json)
            args = json.loads(args_json)
            games = self.baseball_client.get_games_h2h(
                h2h=f"{args['team1id']}-{args['team2id']}", # convert to id-id format
                date=args["date"],
                league=args["league"],
                season=args["season"],
                timezone=args["timezone"]
            )
            print("games", games)
            return games
        return FunctionTool(
            name="head_to_head_games",
            description="Get head-to-head games between two teams",
            params_json_schema=head_to_head_games_function_schema,
            on_invoke_tool=invoke
        )

class BaseballAgent:
    def __init__(self) -> None:
        self.tools = BaseBallAgentTools().get_tools()
        self.agent_client = AgentClient(
            name="Baseball Agent",
            instructions="You are a baseball agent who knows about stats from from 2021 to 2023 (our free api is limited to those date ranges). You can answer questions about baseball.\n\nTeam Ids:\n" + team_id_context + "\nLeague Ids:\n" + league_id_context,
            tools=list(self.tools)
        )
    
    async def run(self, prompt: str) -> str:
        return await self.agent_client.run(prompt)