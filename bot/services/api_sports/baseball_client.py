import http.client
import os
from typing import Dict, List, Optional
from bot.core.logger import get_logger
from bot.utils import concat_url_params
import json
from .baseball_types import (
    CountriesResponse,
    LeaguesResponse,
    OddsBookmakerResponse,
    OddsBookmakerResponseItem,
    TeamsResponse,
    TimezonesResponse, 
    SeasonsResponse, 
    CountriesResponseItem, 
    LeaguesResponseItem, 
    TeamsResponseItem, 
    StandingsResponse,
    StandingsResponseItem,
    StandingsGroupsResponse,
    GamesResponse,
    GameResponseItem,
    GamesH2HResponse,
    H2HGameResponseItem,
    OddsResponse,
    OddsResponseItem,
    OddsBetResponse,
    OddsBetResponseItem,
)
logger = get_logger()

class BaseballClient:
    def __init__(self) -> None:
        self.connection = http.client.HTTPSConnection("v1.baseball.api-sports.io")

    def get_games_h2h(
        self,
        h2h: str,
        date: Optional[str] = None,
        league: Optional[int] = None,
        season: Optional[int] = None,
        timezone: Optional[str] = None
    ) -> List[H2HGameResponseItem]:
        """
        Query the /games/h2h endpoint for head-to-head games between two teams.
        :param h2h: Required. Team ids in 'id-id' format (e.g., '5-6')
        :param date: Optional. Date in 'YYYY-MM-DD'
        :param league: Optional. League id
        :param season: Optional. Season year (4 digits)
        :param timezone: Optional. Timezone string
        :return: List of H2HGameResponseItem
        """
        params = concat_url_params(
            h2h=h2h,
            date=date or "",
            league=str(league) if league else "",
            season=str(season) if season else "",
            timezone=timezone or ""
        )
        headers = self._get_headers()
        self.connection.request("GET", f"/games/h2h?{params}", body=None, headers=headers)
        res = self.connection.getresponse()
        data = res.read()
        parsed: GamesH2HResponse = json.loads(data)
        if parsed.get("errors"):
            logger.error(f"API error in get_games_h2h: {parsed['errors']}")
        return parsed.get("response", [])

    def _get_headers(self) -> Dict[str, str]:
        return {
            'x-rapidapi-key': os.getenv("API_SPORTS_KEY") or "",
            'x-rapidapi-host': os.getenv("API_SPORTS_HOST") or ""
        }
    
    def get_timezones(self) -> List[str]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: TimezonesResponse) -> List[str]:
                return data["response"]
            
            self.connection.request("GET", "/timezone", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get timezones: {e}")
            return []


    def get_seasons(self) -> List[int]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: SeasonsResponse) -> List[int]:
                return data["response"]
            
            self.connection.request("GET", "/seasons", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get seasons: {e}")
            return []

    def get_standings(self,
        league: int,
        season: int,
        team: Optional[int] = None,
        stage: Optional[str] = None,
        group: Optional[str] = None
    ) -> List[StandingsResponseItem]:
        try:
            params = concat_url_params(
                league=str(league),
                season=str(season),
                team=str(team) if team else "",
                stage=str(stage) if stage else "",
                group=str(group) if group else ""
            )
            
            endpoint = f"/standings?{params}"
            headers = self._get_headers()
            payload = ''
            self.connection.request("GET", endpoint, payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            json_data: StandingsResponse = json.loads(data)
            if json_data.get("errors"):
                logger.error(f"Standings API errors: {json_data['errors']}")
                return []
            return json_data.get("response", [])
        except Exception as e:
            logger.error(f"Failed to get standings: {e}")
            return []

    def get_countries(self,
        id: Optional[int] = None,
        name: Optional[str] = None,
        code: Optional[str] = None,
        search: Optional[str] = None,
    ) -> List[CountriesResponseItem]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: CountriesResponse) -> List[CountriesResponseItem]:
                return data["response"] or []
            params = concat_url_params(
                id=str(id) if id else "",
                name=str(name) if name else "",
                code=str(code) if code else "",
                search=str(search) if search else ""
            )
            self.connection.request("GET", f"/countries/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get countries: {e}")
            return []

    def get_leagues(self, 
        id: Optional[int] = None,
        name: Optional[str] = None,
        country_id: Optional[int] = None,
        country: Optional[str] = None,
        type: Optional[str] = None,
        season: Optional[int] = None,
        search: Optional[str] = None,
    ) -> List[LeaguesResponseItem]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: LeaguesResponse) -> List[LeaguesResponseItem]:
                return data["response"] or []
            
            params = concat_url_params(
                id=str(id),
                name=str(name),
                country_id=str(country_id),
                country=str(country),
                type=str(type),
                season=str(season),
                search=str(search)
            )
            
            self.connection.request("GET", f"/leagues/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get leagues: {e}")
            return []

    def get_teams(self, id: Optional[int] = None, 
        name: Optional[str] = None, 
        country_id: Optional[int] = None, 
        country: Optional[str] = None, 
        league: Optional[int] = None, 
        season: Optional[int] = None, 
        search: Optional[str] = None) -> List[TeamsResponseItem]:

        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: TeamsResponse) -> List[TeamsResponseItem]:
                return data["response"] or []

            params = concat_url_params(
                id=str(id),
                name=str(name),
                country_id=str(country_id),
                country=str(country),
                league=str(league),
                season=str(season),
                search=str(search)
            )
            
            self.connection.request("GET", f"/teams/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get teams: {e}")
            return []


    def get_standings_groups(self, league: int, season: int) -> list[str]:
        """
        Get the list of available groups for a league to be used in the standings endpoint.
        :param league: The id of the league
        :param season: The season of the league (4-digit year)
        :return: List of group names (strings)
        """
        try:
            params = concat_url_params(
                league=str(league),
                season=str(season)
            )
            endpoint = f"/standings/groups?{params}"
            headers = self._get_headers()
            payload = ''
            self.connection.request("GET", endpoint, payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            json_data: StandingsGroupsResponse = json.loads(data)
            if json_data.get("errors"):
                logger.error(f"Standings groups API errors: {json_data['errors']}")
                return []
            return json_data.get("response", [])
        except Exception as e:
            logger.error(f"Failed to get standings groups: {e}")
            return []

    # def get_team_statistics(self, 
    #     league_id: int, 
    #     season: str, 
    #     team_id: int, 
    #     date: Optional[str] = None) -> List[TeamsStatisticsResponseItem]:
    #     try:
    #         payload = ''
    #         headers = self._get_headers()
    #         def _parse_response(data: TeamsStatisticsResponse) -> List[TeamsStatisticsResponseItem]:
    #             return data["response"] or []
            
    #         params = concat_url_params(
    #             league=str(league_id),
    #             season=str(season),
    #             team=str(team_id),
    #             date=str(date) if date else ""
    #         )
            
    #         self.connection.request("GET", f"/teams/statistics/?{params}", payload, headers)
    #         res = self.connection.getresponse()
    #         data = res.read()
    #         return _parse_response(json.loads(data))         
    #     except Exception as e:
    #         logger.error(f"Failed to get team statistics: {e}")
    #         return []

    def get_games(
        self,
        id: Optional[int] = None,
        date: Optional[str] = None,
        league: Optional[int] = None,
        season: Optional[int] = None,
        team: Optional[int] = None,
        timezone: Optional[str] = None
    ) -> list[GameResponseItem]:
        """
        Get games from the API-sports baseball API. At least one parameter must be provided.
        """
        try:
            payload = ''
            headers = self._get_headers()
            params = concat_url_params(
                id=str(id) if id else "",
                date=date,
                league=str(league) if league else "",
                season=str(season) if season else "",
                team=str(team) if team else "",
                timezone=timezone
            )
            if not params:
                raise ValueError("At least one parameter must be provided to get_games.")
            endpoint = f"/games?{params}"
            self.connection.request("GET", endpoint, payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            json_data: GamesResponse = json.loads(data)
            if json_data.get("errors"):
                logger.error(f"Games API errors: {json_data['errors']}")
                return []
            return json_data.get("response", [])
        except Exception as e:
            logger.error(f"Failed to get games: {e}")
            return []
            
    def get_odds(self,
        id: Optional[int] = None,
        league: Optional[int] = None,
        season: Optional[int] = None,
        team: Optional[int] = None,
        date: Optional[str] = None,
        timezone: Optional[str] = None
    ) -> List[OddsResponseItem]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: OddsResponse) -> List[OddsResponseItem]:
                return data["response"]
            
            params = concat_url_params(
                id=str(id) if id else "",
                league=str(league) if league else "",
                season=str(season) if season else "",
                team=str(team) if team else "",
                date=str(date) if date else "",
                timezone=str(timezone) if timezone else ""
            )
            
            self.connection.request("GET", f"/odds/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get odds: {e}")
            return []
        
    def get_odds_bets(self,
        id: Optional[int] = None,
        name: Optional[str] = None,
        timezone: Optional[str] = None
    ) -> List[OddsBetResponseItem]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: OddsBetResponse) -> List[OddsBetResponseItem]:
                return data["response"]
            
            params = concat_url_params(
                id=str(id) if id else "",
                name=name,
                timezone=timezone
            )
            
            self.connection.request("GET", f"/odds/bets/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get odds bets: {e}")
            return []
        

    def get_odds_bookmakers(self,
        id: Optional[int] = None,
        name: Optional[str] = None,
        timezone: Optional[str] = None
    ) -> List[OddsBookmakerResponseItem]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: OddsBookmakerResponse) -> List[OddsBookmakerResponseItem]:
                return data["response"] or []
            
            params = concat_url_params(
                id=str(id) if id else "",
                name=name,
                timezone=timezone
            )
            
            self.connection.request("GET", f"/odds/bookmakers/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get odds bookmakers: {e}")
            return []
        