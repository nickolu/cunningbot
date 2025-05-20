from typing import TypedDict, List, Optional, Dict



class TimezonesResponse(TypedDict):
    get: str
    parameters: List[str]
    errors: List[str]
    results: int
    response: List[str]


class SeasonsResponse(TypedDict):
    get: str
    parameters: List[str]
    errors: List[str]
    results: int
    response: List[int]


class CountriesResponseItem(TypedDict):
    id: int
    code: str
    name: str
    flag: str


class CountriesResponse(TypedDict):
    get: str
    parameters: List[str]
    errors: List[str]
    results: int
    response: List[CountriesResponseItem]
    parameters: {
        "code": str
    }


class LeagueSeasonsResponseItem(TypedDict):
    season: int
    current: bool
    start: str
    end: str


class LeaguesResponseItem(TypedDict):
    id: int
    name: str
    type: str
    logo: str
    country: CountriesResponseItem
    seasons: List[LeagueSeasonsResponseItem]

class LeaguesReponseParameters(TypedDict):
    id: str
    season: str

class LeaguesResponse(TypedDict):
    get: str
    parameters: LeaguesReponseParameters
    errors: List[str]
    results: int
    response: List[LeaguesResponseItem]


class TeamsResponseItem(TypedDict):
    id: int
    name: str
    logo: str
    country: CountriesResponseItem

class TeamsResponseParams(TypedDict):
    id: str

class TeamsResponse(TypedDict):
    get: str
    parameters: Dict[str, str]
    errors: List[str]
    results: int
    response: List[TeamsResponseItem]

class TeamsStatisticsResponseParams(TypedDict):
    league: str
    team: str
    season: str
    

class TeamsStatisticsResponseItem(TypedDict):
    get: str
    parameters: TeamsStatisticsResponseParams
    errors: List[str]
    results: int
    response: List[TeamsStatisticsResponseItem]

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
    for_: int
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