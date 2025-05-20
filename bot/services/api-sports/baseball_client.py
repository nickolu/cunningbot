import http.client
from typing import List
from bot.core.logger import get_logger
from bot.utils import concat_url_params
import json
from .baseball_types import (
    TimezonesResponse, 
    SeasonsResponse, 
    CountriesResponseItem, 
    LeaguesResponseItem, 
    TeamsResponseItem, 
    TeamsStatisticsResponseItem, 
    TeamsStatisticsResponse,
    StandingsResponse,
    StandingsResponseItem,
    StandingsGroupsResponse,
    GamesResponse,
    GameResponseItem
)
logger = get_logger()

class BaseballClient:
    def __init__(self):
        self.connection = http.client.HTTPSConnection("v1.baseball.api-sports.io")

    def _get_headers(self):
        return {
            'x-rapidapi-key': 'XxXxXxXxXxXxXxXxXxXxXxXx',
            'x-rapidapi-host': 'v1.baseball.api-sports.io'
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
        team: int = None,
        stage: str = None,
        group: str = None
    ) -> list[list[StandingsResponseItem]]:
        try:
            params = concat_url_params(
                league=league,
                season=season,
                team=team,
                stage=stage,
                group=group
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
                return data["response"]
            
            self.connection.request("GET", f"/countries/?{concat_url_params(id=id, name=name, code=code, search=search)}", payload, headers)
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
                return data["response"]
            
            params = concat_url_params(
                id=id,
                name=name,
                country_id=country_id,
                country=country,
                type=type,
                season=season,
                search=search
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
                return data["response"]

            params = concat_url_params(
                id=id,
                name=name,
                country_id=country_id,
                country=country,
                league=league,
                season=season,
                search=search
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
                league=league,
                season=season
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

    def get_team_statistics(self, 
        league_id: int, 
        season: str, 
        team_id: int, 
        date: Optional[str] = None) -> List[TeamsStatisticsResponseItem]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: TeamsStatisticsResponse) -> List[TeamsStatisticsResponseItem]:
                return data["response"]
            
            params = concat_url_params(
                league=league_id,
                season=season,
                team=team_id,
                date=date
            )
            
            self.connection.request("GET", f"/teams/statistics/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get team statistics: {e}")
            return []
        
    def get_standings(self,
        league: int,
        season: int,
        team: int = None,
        stage: str = None,
        group: str = None
    ) -> List[StandingsResponseItem]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: StandingsResponse) -> List[StandingsResponseItem]:
                return data["response"]
            
            params = concat_url_params(
                league=league,
                season=season,
                team=team,
                stage=stage,
                group=group
            )
            
            self.connection.request("GET", f"/standings/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get standings: {e}")
            return []


    def get_games(
        self,
        id: int = None,
        date: str = None,
        league: int = None,
        season: int = None,
        team: int = None,
        timezone: str = None
    ) -> list[GameResponseItem]:
        """
        Get games from the API-sports baseball API. At least one parameter must be provided.
        """
        try:
            payload = ''
            headers = self._get_headers()
            params = concat_url_params(
                id=id,
                date=date,
                league=league,
                season=season,
                team=team,
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

    def get_standings_stages(self,
        league: int,
        season: int
    ) -> List[StandingsStagesResponseItem]:
        try:
            payload = ''
            headers = self._get_headers()
            def _parse_response(data: StandingsStagesResponse) -> List[StandingsStagesResponseItem]:
                return data["response"]
            
            params = concat_url_params(
                league=league,
                season=season
            )
            
            self.connection.request("GET", f"/standings/stages/?{params}", payload, headers)
            res = self.connection.getresponse()
            data = res.read()
            return _parse_response(json.loads(data))         
        except Exception as e:
            logger.error(f"Failed to get standings stages: {e}")
            return []
            