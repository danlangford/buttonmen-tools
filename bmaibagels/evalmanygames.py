#!/usr/env python3

from collections import OrderedDict
from operator import getitem

import bmutils

bm = bmutils.BMClientParser(".bmrc", "www")
bm.verify_login()
count = 0

ranges = [(91881, 92270), (92314, 92707)]


def id_in_any_range(id):
  for range in ranges:
    if range[0] <= id <= range[1]:
      return True
  return False


def doit():
  global count
  print(f"hi {bm.username}")

  search_results = bm.wrap_search_game_history(
      sortColumn="lastMove",
      searchDirection="ASC",
      numberOfResults=1000,
      page=1,
      status="COMPLETE",
      playerNameA="BMAIBagels",
      playerNameB="Nala",
  )

  my_games = []
  bresults = {}
  presults = {}

  for game in search_results["games"]:
    if id_in_any_range(game["gameId"]):
      my_games.append(game)

  print(my_games)
  print(len(my_games))

  # all games between Soldiers and The Core = 390
  # games within Soldiers = 169
  # games within The Core = 225
  # total games 784

  for game in my_games:
    play_a = game["playerNameA"]
    play_b = game["playerNameB"]
    butt_a = game["buttonNameA"]
    butt_b = game["buttonNameB"]
    won_a = game["roundsWonA"]
    won_b = game["roundsWonB"]
    target = game["targetWins"]

    for play_i in [play_a, play_b]:
      if play_i not in presults:
        presults[play_i] = {"w": 0, "l": 0, "count": 0, "rate": 0}
      presults[play_i]["count"] += 1
    for butt_i in [butt_a, butt_b]:
      if butt_i not in bresults:
        bresults[butt_i] = {"w": 0, "l": 0, "count": 0, "rate": 0}
      bresults[butt_i]["count"] += 1

    if won_a == target:
      presults[play_a]["w"] += 1
      presults[play_b]["l"] += 1
      bresults[butt_a]["w"] += 1
      bresults[butt_b]["l"] += 1
    elif won_b == target:
      presults[play_a]["l"] += 1
      presults[play_b]["w"] += 1
      bresults[butt_a]["l"] += 1
      bresults[butt_b]["w"] += 1
    else:
      raise RuntimeError(
          "i dont know what happened these games are supposed to be complete")

    for play_i in [play_a, play_b]:
      presults[play_i][
          "rate"] = presults[play_i]["w"] / presults[play_i]["count"]
    for butt_i in [butt_a, butt_b]:
      bresults[butt_i][
          "rate"] = bresults[butt_i]["w"] / bresults[butt_i]["count"]

  ordered_bresults = OrderedDict(
      sorted(
          bresults.items(), key=lambda x: getitem(x[1], "rate"), reverse=True))
  ordered_presults = OrderedDict(
      sorted(
          presults.items(), key=lambda x: getitem(x[1], "rate"), reverse=True))

  print(ordered_bresults)
  print(ordered_presults)

  for key, value in ordered_bresults.items():
    binfo = bm.wrap_load_button_data(key)
    rate = "%.2f" % (value["rate"] * 100)
    print(f"{key.ljust(16)} {binfo['recipe'].ljust(22)} {rate.rjust(10)}%")

  for key, value in ordered_presults.items():
    rate = "%.2f" % (value["rate"] * 100)
    print(f"{key} {rate}%")

    # SOLDIERS (156)

    # starts at game 92314 #1
    # made it to game 92440  #127
    # printed 127 then Error
    # tried again, after 127
    # picks up at game 92441 #128
    # ends at game 92469 #156

    # THE CORE (210)

    # start at 92470 #1
    # ends at 92679 #210

    # then for fun mirror matches that probably wont go into "tournament results"
    # 92680-92692
    # 92693-92707


if __name__ == "__main__":
  doit()
