from math import gamma
from .mlb_context_client import MlbClient

client = MlbClient()
# print("team ids")
# print([(team.name, team.id) for team in client.get_teams()])
# print("league ids")
# print([(league['id'], league['name']) for league in client.get_leagues()])


# print(client.get_standings(league_id=103, season=2025))
# print(client.get_schedule(date="2025-05-22"))

# print(client.get_team_info(team_id=133))

# print(client.get_player_info(player_id=544756))

# print(client.get_boxscore(game_id=715757))

# print(client.get_game_highlights(game_id=715757))

# print(client.get_game_plays(game_id=715757))

# print(client.get_scoring_plays(game_id=715757))

# print(client.get_linescore(game_id=715757))

# print(client.get_latest_season(sport_id=2))

print(client.lookup_player("Nola"))

# print(client.get_player_stat_data(person_id=544756, group="hitting", type="season", season=2025))





