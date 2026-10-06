#!/usr/bin/env python3

# GAME DATA
#
# Print out data about a game.

import argparse
import json
import sys

import yaml

import bmutils
from bmaipy import BUTTON_SPECIALS


def parse_args():
  parser = argparse.ArgumentParser(description="Print out data about a game.")

  parser.add_argument("gameid", help="game number")

  parser.add_argument(
      "--format",
      "-f",
      choices=["json", "yaml", "bmai"],
      default="yaml",
      help="output data format (default: yaml)",
  )

  # Add general optional arguments.

  parser.add_argument(
      "--site", "-s", default="www", help="site to check ('www' by default)")

  parser.add_argument(
      "--config",
      "-c",
      default=".bmrc",
      help="config file containing site parameters")

  # Return the parser.

  return parser.parse_args()


class NoAliasDumper(yaml.SafeDumper):

  def ignore_aliases(self, _data):
    return True


# Ply 1 with 40x the simulations tied ply 2 on classic buttons and beat it on
# Turbo, Fire and Poison buttons, in a quarter of the time (BMAIR STRENGTH.md).
DEFAULT_PLY = 1
# Exact play from this many dice in total; more costs too much time (STRENGTH.md).
ENDGAME_DICE = 4
SEARCH_SETTINGS = {
    1: (4000, 200, 16000),
    2: (100, 5, 400),
}


class bmai(object):

  def recipe(d):
    r = d["recipe"]

    # remove () around die, except for option
    if "," not in r:
      r = r.replace("(", "").replace(")", "")

    # if sides selected for swing or option, include them
    if ("/" in r or "Swing" in d["description"]) and d["sides"]:
      # for Twin we want to describe the die size of each die, not the total
      if 'Twin' in d["properties"]:
        r += "-" + str(d["subdieArray"][0]["sides"]) # which should also be d["sides"]/2
      else:
        r += "-" + str(d["sides"])

    # include value as needed
    if d["value"]:
      r += f":{d['value']}"
      if "Dizzy" in d["properties"]:
        r += "d"

    return r

  def dump(game, ply=3, report_sims=0, specials=False, endgame=False):
    retval = "mode native\nworkers auto\nfire_overshooting on\n"
    retval += f"game {game['maxWins']}\n"
    if game["gameState"] == "START_TURN":
      retval += "fight\n"
    elif game["gameState"] == "SPECIFY_DICE":
      retval += "preround\n"
    elif game["gameState"] == "CHOOSE_RESERVE_DICE":
      retval += "reserve\n"
    elif game["gameState"] == "CHOOSE_AUXILIARY_DICE":
      retval += "aux\n"
    elif game["gameState"] == "REACT_TO_INITIATIVE":
      nestedskilllists = [d["skills"] for d in game["player"]["activeDieArray"]]
      skills = set(
          [skill for skilllist in nestedskilllists for skill in skilllist])
      if "Focus" in skills:
        retval += "focus\n"
      elif "Chance" in skills:
        retval += "chance\n"
      else:
        retval += "____I_DONT_KNOW_REACT_INITIATIVE_"
    else:
      retval += f"unhandled_{game['gameState']}\n"
    if game["player"]["waitingOnAction"]:
      player0, player1 = game["player"], game["opponent"]
    else:
      player0, player1 = (game["opponent"], game["player"])
    retval += f"player 0 {len(player0['activeDieArray'])} {player0['roundScore'] if player0['roundScore'] else 0}\n"
    for d in player0["activeDieArray"]:
      retval += f"{bmai.recipe(d)}\n"
    retval += f"player 1 {len(player1['activeDieArray'])} {player1['roundScore'] if player1['roundScore'] else 0}\n"
    for d in player1["activeDieArray"]:
      retval += f"{bmai.recipe(d)}\n"
    if specials:
      for idx, player in enumerate((player0, player1)):
        names = BUTTON_SPECIALS.get(player["button"]["name"])
        if names:
          retval += f"special {idx} {' '.join(names)}\n"
    max_sims, min_sims, maxbranch = SEARCH_SETTINGS.get(ply, SEARCH_SETTINGS[2])
    retval += f"ply {ply}\nmax_sims {max_sims}\nmin_sims {min_sims}\nmaxbranch {maxbranch}\n"
    retval += "cull on\nplayout quick\nturbo_accuracy 1\n"
    if endgame:
      retval += f"endgame {ENDGAME_DICE}\n"
    # BMAIR copies the search settings when it reads `ai`, so it comes last.
    retval += "ai 0 montecarlo\n"
    if report_sims:
      retval += f"report_sims {report_sims}\n"
    retval += "surrender off\n"
    retval += "getaction\n"
    retval += "quit\n"
    return retval


class GameData(object):

  def __init__(self, client, format=None):
    self.client = client
    self.format = format
    if not self.client.verify_login():
      print("ERROR: Could not log in")
      sys.exit(1)

  def fetch(self, gameid, format_override=None):
    game = self.client.wrap_load_game_data(gameid)
    fmt = format_override or self.format
    if fmt == "json":
      return json.dumps(game, indent=1, sort_keys=True)
    elif fmt == "yaml":
      return yaml.dump(game, Dumper=NoAliasDumper)
    elif fmt == "bmai":
      return bmai.dump(game)
    elif fmt is None:
      return game
    else:
      raise Exception(f"format {fmt} not supported")


if __name__ == "__main__":
  args = parse_args()
  bmclient = bmutils.BMClientParser(args.config, args.site)
  game_data = GameData(bmclient, format=args.format)
  print(game_data.fetch(args.gameid))
