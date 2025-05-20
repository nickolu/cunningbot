
TimezonesResponse = {
    "get": str,
    "parameters": List[str],
    "errors": List[str],
    "results": int,
    "response": List[str]
}

SeasonsResponse = {
    "get": str,
    "parameters": List[str],
    "errors": List[str],
    "results": int,
    "response": List[int]
}

CountriesResponseItem = {
    "id": int,
    "code": str,
    "name": str,
    "flag": str
}

CountriesResponse = {
    "get": str,
    "parameters": List[str],
    "errors": List[str],
    "results": int,
    "response": List[CountriesResponseItem],
    "parameters": {
        "code": str
    }
}

LeagueSeasonsResponseItem = {
    "season": int,
    "current": bool,
    "start": str,
    "end": str
}

LeaguesResponseItem = {
    "id": int,
    "name": str,
    "type": str,
    "logo": str,
    "country": CountriesResponseItem,
    "seasons": List[LeagueSeasonsResponseItem]
}

LeaguesResponse = {
    "get": str,
    "parameters": {
        "id": str,
        "season": str
    },
    "errors": List[str],
    "results": int,
    "response": List[str]
}

TeamsResponseItem = {
    "id": int,
    "name": str,
    "logo": str,
    "country": CountriesResponseItem
}

TeamsResponse = {
    "get": str,
    "parameters": {
        "league": str,
        "season": str
    },
    "errors": List[str],
    "results": int,
    "response": List[TeamsResponseItem]
}

TeamStatisticsTeamResponseTeam = {
    "id": int,
    "name": str,
    "logo": str,
}

TeamStatisticsPlayedGameResponseItem = {
    "home": int,
    "away": int,
    "all": int,
}

TeamStatisticsGameResponseItem = {
    "played": List[TeamStatisticsPlayedGameResponseItem],
    "wins": List[TeamStatisticsPlayedGameResponseItem],
    "losses": List[TeamStatisticsPlayedGameResponseItem],
}

TeamStatisticsPointsResponseItem = {
    "total": int,
    "average": int,
}

TeamStatisticsPointsResponse = {
    "for": TeamStatisticsPointsResponseItem,
    "against": TeamStatisticsPointsResponseItem,
}

TeamsStatisticsResponseItem = {
    "country": CountriesResponseItem,
    "league": LeaguesResponseItem,
    "team": TeamStatisticsTeamResponseTeam,
    "games": TeamStatisticsGameResponseItem,
    "points": TeamStatisticsPointsResponse,
}

TeamsStatisticsResponse = {
    "get": str,
    "parameters": {
        "id": str,
        "season": str
    },
    "errors": List[str],
    "results": int,
    "response": List[TeamsStatisticsResponseItem]
}

from typing import TypedDict, List, Optional, Dict

class StandingsGroup(TypedDict):
    name: str

class StandingsWinLose(TypedDict):
    total: int
    percentage: str

class StandingsGames(TypedDict):
    played: int
    win: StandingsWinLose
    lose: StandingsWinLose

class StandingsPoints(TypedDict):
    for_: int  # 'for' is a reserved word in Python, use 'for_' in code
    against: int

class StandingsTeam(TypedDict):
    id: int
    name: str
    logo: str

class StandingsLeague(TypedDict):
    id: int
    name: str
    type: str
    logo: str
    season: int

class StandingsCountry(TypedDict):
    id: int
    name: str
    code: str
    flag: str

class StandingsResponseItem(TypedDict, total=False):
    position: int
    stage: str
    group: StandingsGroup
    team: StandingsTeam
    league: StandingsLeague
    country: StandingsCountry
    games: StandingsGames
    points: StandingsPoints
    form: Optional[str]
    description: Optional[str]

class StandingsResponse(TypedDict):
    get: str
    parameters: Dict[str, str]
    errors: List[str]
    results: int
    response: List[List[StandingsResponseItem]]