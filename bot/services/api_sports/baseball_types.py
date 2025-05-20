from typing import TypedDict, List, Optional, Dict, Union


class GameStatus(TypedDict):
    long: str
    short: str

class GameCountry(TypedDict):
    id: int
    name: str
    code: str
    flag: str

class GameLeague(TypedDict):
    id: int
    name: str
    type: str
    logo: str
    season: int

class GameTeam(TypedDict):
    id: int
    name: str
    logo: str

class GameTeams(TypedDict):
    home: GameTeam
    away: GameTeam

class GameInnings(TypedDict, total=False):
    # innings 1-9, extra are all optional and can be null
    # Use Union[int, None] so null is supported
    # The keys are string numbers ('1', '2', ...) and 'extra'
    # API sample: {"1": 0, ..., "extra": null}
    # Use str keys to match JSON
    # TypedDict can't do arbitrary keys, so we list common ones
    # (Python 3.11+ supports NotRequired, but for max compatibility, use total=False)
    # If you use Python 3.11+, can use NotRequired
    # Here, all are optional
    _1: Optional[int]
    _2: Optional[int]
    _3: Optional[int]
    _4: Optional[int]
    _5: Optional[int]
    _6: Optional[int]
    _7: Optional[int]
    _8: Optional[int]
    _9: Optional[int]
    extra: Optional[int]

class GameScoreSide(TypedDict):
    hits: int
    errors: int
    innings: Dict[str, Optional[int]]
    total: int

class GameScores(TypedDict):
    home: GameScoreSide
    away: GameScoreSide

class GameResponseItem(TypedDict):
    id: int
    date: str
    time: str
    timestamp: int
    timezone: str
    week: Optional[Union[int, str]]
    status: GameStatus
    country: GameCountry
    league: GameLeague
    teams: GameTeams
    scores: GameScores

class GamesH2HResponse(TypedDict):
    get: str
    parameters: Dict[str, Union[str, int]]
    errors: List[str]
    results: int
    response: List[GameResponseItem]

# Alias for a single H2H game (identical to GameResponseItem, but explicit for clarity)
H2HGameResponseItem = GameResponseItem

class GamesResponse(TypedDict):
    get: str
    parameters: Dict[str, Union[str, int]]
    errors: List[str]
    results: int
    response: List[GameResponseItem]

class StandingsGroupsParameters(TypedDict):
    league: str
    season: str

class StandingsGroupsResponse(TypedDict):
    get: str
    parameters: StandingsGroupsParameters
    errors: List[str]
    results: int
    response: List[str]


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

class CountriesResponseParameters(TypedDict):
    code: str

class CountriesResponse(TypedDict):
    get: str
    parameters: CountriesResponseParameters
    errors: List[str]
    results: int
    response: List[CountriesResponseItem]

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
    response: List[StandingsResponseItem]

class StandingsStagesParameters(TypedDict):
    league: str
    season: str

class StandingsStagesResponse(TypedDict):
    get: str
    parameters: StandingsStagesParameters
    errors: List[str]
    results: int
    response: List[str]


class OddsResponseItem(TypedDict, total=False):
    league: Optional[Dict[str, str]]
    game: Optional[Dict[str, str]]
    bookmakers: Optional[List[Dict[str, str]]]

class OddsResponse(TypedDict):
    get: str
    parameters: Dict[str, str]
    errors: List[str]
    results: int
    response: List[OddsResponseItem]


class OddsBetResponseItem(TypedDict, total=False):
    id: int
    name: str

class OddsBetResponse(TypedDict):
    get: str
    parameters: Dict[str, str]
    errors: List[str]
    results: int
    response: List[OddsBetResponseItem]


class OddsBookmakerResponseItem(TypedDict, total=False):
    id: int
    name: str

class OddsBookmakerResponse(TypedDict):
    get: str
    parameters: Dict[str, str]
    errors: List[str]
    results: int
    response: List[OddsBookmakerResponseItem]