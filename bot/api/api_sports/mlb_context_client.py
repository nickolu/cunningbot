from typing import Optional, Dict, Any, List, cast

from mlbstatsapi import Mlb, mlb_api

class MlbClient:
    """
    Client for interacting with the MLB Stats API via mlbstatsapi library.
    """
    def __init__(self) -> None:
        self.mlb = Mlb()

    def get_standings(self, league_id: int, season: int) -> str:
        """
        Retrieve standings for a given league and season as plain text.
        """
        data = cast(List[mlb_api.Standings], self.mlb.get_standings(league_id, season=str(season)))

        if not data or len(data) == 0:
            return "No standings found."
        lines = []
        for record in data:
            div = record.division.id
            lines.append(f"Division: {div}")
            for teamrec in record.teamrecords:
                team = teamrec.team
                name = team.name
                wid = teamrec.wins
                lid = teamrec.losses
                pct = teamrec.winningpercentage
                gb = teamrec.gamesback
                lines.append(f"  {name}: W {wid} L {lid} Pct {pct} GB {gb}")
        return "\n".join(lines)


    def get_schedule(
        self,
        date: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        sport_id: int = 1,
        team_id: Optional[int] = None,
    ) -> str:
        """
        Retrieve game schedule by date, date range, sport, or team as plain text.
        """
        data = cast(mlb_api.Schedule, self.mlb.get_schedule(
            date=date,
            start_date=start_date,
            end_date=end_date,
            sport_id=sport_id,
            team_id=team_id,
        ))
        if not data or not data.dates:
            return "No games found."
        lines = []
        for dateblock in data.dates:
            date_str = dateblock.date
            lines.append(f"Date: {date_str}")
            for game in dateblock.games:
                gid = game.gamepk
                status = game.status.detailedstate
                home = game.teams.home.team.name
                away = game.teams.away.team.name
                daynight = game.daynight
                lines.append(f"  Game {gid}: {away} at {home} [{status}] {daynight}")
        return "\n".join(lines)


    def get_team_info(
        self,
        team_id: int,
        season: Optional[int] = None,
        sport_id: Optional[int] = None,
        hydrate: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> str:
        """
        Retrieve information for a specific team as plain text.
        """
        params: Dict[str, Any] = {}
        if season is not None:
            params["season"] = str(season)
        if sport_id is not None:
            params["sport_id"] = sport_id
        if hydrate is not None:
            params["hydrate"] = hydrate
        if fields is not None:
            params["fields"] = fields
        team = cast(mlb_api.Team, self.mlb.get_team(team_id, **params))
        if not team:
            return "No team found."
        name = team.name
        abbr = team.abbreviation
        return f"{name} ({abbr})"


    def get_player_info(self, player_id: int) -> str:
        """
        Retrieve information for a specific player as plain text.
        """
        data = cast(mlb_api.Person, self.mlb.get_person(player_id))
        if not data:
            return "No player found."
        p = data
        name = p.fullname
        pos = p.primaryposition.abbreviation
        team = p.currentteam
        dob = p.birthdate
        bats = p.batside.description
        throws = p.pitchhand.description
        return f"{name} | Pos: {pos} | Team: {team} | Born: {dob} | Bats: {bats} | Throws: {throws}"


    def get_boxscore(
        self,
        game_id: int,
        timecode: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> str:
        """
        Retrieve boxscore for a specific game as plain text.
        """
        params: Dict[str, Any] = {}
        if timecode is not None:
            params["timecode"] = timecode
        if fields is not None:
            params["fields"] = fields
        data = cast(mlb_api.BoxScore, self.mlb.get_game_box_score(game_id, **params))
        if not data:
            return "No boxscore found."
        lines = []
        for side in ('home', 'away'):
            if side == 'home':
                team = data.teams.home.team.name
                runs = data.teams.home.teamstats['batting']['runs']
                hits = data.teams.home.teamstats['batting']['hits']
                errors = data.teams.home.teamstats['fielding']['errors']
            else:
                team = data.teams.away.team.name
                runs = data.teams.away.teamstats['batting']['runs']
                hits = data.teams.away.teamstats['batting']['hits']
                errors = data.teams.away.teamstats['fielding']['errors']
            lines.append(f"{team}: R {runs} H {hits} E {errors}")
        return "\n".join(lines)


    def get_game_plays(self, game_id: int) -> str:
        """
        Retrieve play-by-play for a specific game as plain text.
        """
        game = cast(mlb_api.Game, self.mlb.get_game(game_id))
      
        if not game.livedata.plays.allplays:
            return "No plays found."
        lines = []
        for item in game.livedata.plays.allplays:
            title = item.result.description
            desc = item.about.inning
            lines.append(f"{title}: {desc}")
        return "\n".join(lines)


    def get_game_pace(self, season: int, sport_id: int = 1) -> str:
        """
        Retrieve game pace statistics for a given season as plain text.
        """
        data = self.mlb.get_gamepace(str(season), sport_id=sport_id)
        if not data or 'gamePace' not in data:
            return "No game pace data found."
        pace = data['gamePace']
        return f"Game Pace: {pace}"


    def get_scoring_plays(
        self,
        game_id: int,
        eventType: Optional[str] = None,
        timecode: Optional[str] = None,
        fields: Optional[str] = None,
    ) -> str:
        """
        Retrieve play-by-play scoring events for a game as plain text.
        """
        params: Dict[str, Any] = {}
        if eventType is not None:
            params["eventType"] = eventType
        if timecode is not None:
            params["timecode"] = timecode
        if fields is not None:
            params["fields"] = fields
        data = cast(mlb_api.Game, self.mlb.get_game(game_id, **params))
        if not data.livedata.plays.allplays:
            return "No scoring plays found."
        lines = []
        for play in data.livedata.plays.allplays:
            desc = play.result.description
            inning = play.about.inning
            lines.append(f"Inning {inning}: {desc}")
        return "\n".join(lines)


    def get_linescore(self, game_id: int) -> str:
        """
        Retrieve line score for a specific game as plain text.
        """
        linescore = cast(mlb_api.Linescore, self.mlb.get_game_line_score(game_id))
        if not linescore:
            return "No linescore found."
        lines = []
        for side in ('home', 'away'):
            if side == 'home':
                team = linescore.defense.team.name
                runs = linescore.teams.home.runs
                hits = linescore.teams.home.hits
                errors = linescore.teams.home.errors
                leftonbase = linescore.teams.home.leftonbase
                iswinner = linescore.teams.home.iswinner
            else:
                team = linescore.offense.team.name
                runs = linescore.teams.away.runs
                hits = linescore.teams.away.hits
                errors = linescore.teams.away.errors
                leftonbase = linescore.teams.away.leftonbase
                iswinner = linescore.teams.away.iswinner
            lines.append(f"{team}: R {runs}, H {hits}, E {errors}, L {leftonbase}, W {iswinner}")
        return "\n".join(lines)


    def get_latest_season(self, sport_id: int = 1) -> str:
        """
        Retrieve data about the latest season for a given sport as plain text.
        """
        data = self.mlb.get_seasons()

        if not data:
            return "No season found."
        latest_season = data[-1]
        seasonid = latest_season.seasonid
        start_date = latest_season.seasonstartdate
        end_date = latest_season.seasonenddate
        return f"Season: {seasonid} Start Date: {start_date} | End Date: {end_date}"

    def lookup_player(
        self,
        lookup_value: str,
        game_type: str = "R",
        season: Optional[int] = None,
        sport_id: int = 1,
    ) -> str:
        """
        Retrieve player data by lookup value as plain text.
        """
        params: Dict[str, Any] = {"game_type": game_type, "sport_id": sport_id}
        if season is not None:
            params["season"] = str(season)
        data = cast(mlb_api.Person, self.mlb.get_person(lookup_value, **params))
        if not data:
            return "No player found."
        player = data
        name = player.fullname
        pos = player.primaryposition.abbreviation
        team = player.currentteam
        dob = player.birthdate
        bats = player.batside.description
        throws = player.pitchhand.description
        return f"{name} | Pos: {pos} | Team: {team} | Born: {dob} | Bats: {bats} | Throws: {throws}"


    def lookup_team(
        self,
        lookup_value: str,
        sport_id: int = 1,
    ) -> str:
        """
        Retrieve team data by lookup value as plain text.
        """
        data = self.mlb.lookup_team(lookup_value, sport_id=sport_id)
        if not data or 'teams' not in data or not data['teams']:
            return "No team found."
        t = data['teams'][0]
        name = t.get('name')
        abbr = t.get('abbreviation')
        return f"{name} | Abbr: {abbr}"


    def get_teams(
        self,
        sport_id: int = 1,
        season: Optional[int] = None,
        active_status: Optional[str] = None,
    ) -> str:
        """
        Retrieve team info (id, name, etc.) for a given sport/season as plain text.
        """
        params: Dict[str, Any] = {}
        if season is not None:
            params["season"] = str(season)
        if active_status is not None:
            params["activeStatus"] = active_status
        teams = self.mlb.get_teams(sport_id=sport_id, **params)
        lines = []
        for team in teams:
            tid = getattr(team, 'id', None) or (team.get('id') if isinstance(team, dict) else None)
            name = getattr(team, 'name', None) or (team.get('name') if isinstance(team, dict) else None)
            abbr = getattr(team, 'abbreviation', None) or (team.get('abbreviation') if isinstance(team, dict) else None)
            line = f"id: {tid} | name: {name} | abbr: {abbr}"
            lines.append(line.strip())
        return "\n".join(lines)


    def get_leagues(self, sport_id: int = 1) -> str:
        """
        Retrieve league info (id, name, etc.) for a given sport as plain text.
        """
        leagues = self.mlb.get_leagues(sportId=sport_id)
        lines = []
        for league in leagues:
            lid = getattr(league, 'id', None) or (league.get('id') if isinstance(league, dict) else None)
            name = getattr(league, 'name', None) or (league.get('name') if isinstance(league, dict) else None)
            abbr = getattr(league, 'abbreviation', None) or (league.get('abbreviation') if isinstance(league, dict) else None)
            line = f"id: {lid} | name: {name} | abbr: {abbr}"
            lines.append(line.strip())
        return "\n".join(lines)
