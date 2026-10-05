#!/usr/bin/env python3
"""Predict the current-round and full-game winners for a Button Weavers game."""

import argparse
import re
import secrets

from bmair_release import resolve_bmair
from bmair_simulation import (
    ButtonSpec,
    infer_round_probability,
    match_win_probability,
    normalize_recipe,
    parse_win_percent,
    run_bmair,
    simulate_balanced_matchup,
)
import bmutils
from game_data import bmai


ACTION_RE = re.compile(
    r"(?:^|\n)action\n((?:[^\n]+\n?)+?)\Z", re.MULTILINE)


def parse_args(argv=None):
  parser = argparse.ArgumentParser(
      description=(
          "Ask BMAIR who is favored in the current round, then simulate the "
          "two buttons in complete first-to-three matches."))
  parser.add_argument("game_id", type=int)
  parser.add_argument("-c", "--config", default=".bmrc")
  parser.add_argument("-s", "--site", default="www")
  parser.add_argument("-b", "--binary")
  parser.add_argument(
      "--bmair-update", choices=("auto", "never", "force"), default="auto")
  parser.add_argument("--matches", type=int, default=100)
  parser.add_argument("--workers", type=int, default=4)
  parser.add_argument("--round-ply", type=int, default=3)
  parser.add_argument("--round-min-sims", type=int, default=5)
  parser.add_argument("--round-max-sims", type=int, default=100)
  parser.add_argument("--round-max-branch", type=int, default=400)
  parser.add_argument("--simulation-ply", type=int, default=2)
  parser.add_argument("--min-sims", type=int, default=5)
  parser.add_argument("--max-sims", type=int, default=20)
  parser.add_argument("--max-branch", type=int, default=400)
  parser.add_argument("--timeout", type=int, default=600)
  parser.add_argument("--chunk-size", type=int, default=1)
  parser.add_argument("--heartbeat", type=int, default=30)
  parser.add_argument("--seed", type=int)
  parser.add_argument(
      "--quick", action="store_true",
      help="print the live round and quick game forecast without full simulations")
  parser.add_argument(
      "--debug", action="store_true",
      help="print the exact current-round request and full-match settings")
  return parser.parse_args(argv)


def replace_setting(request, name, value):
  return re.sub(
      rf"^{re.escape(name)}\s+[^\n]+$", f"{name} {value}", request,
      flags=re.MULTILINE)


def current_round_request(game, args):
  request = bmai.dump(game, ply=args.round_ply)
  request = replace_setting(request, "workers", args.workers)
  request = replace_setting(request, "min_sims", args.round_min_sims)
  request = replace_setting(request, "max_sims", args.round_max_sims)
  request = replace_setting(request, "maxbranch", args.round_max_branch)
  if args.seed is not None:
    request = request.replace("game ", f"seed {args.seed}\ngame ", 1)
  return request


def action_summary(output):
  match = ACTION_RE.search(output)
  if not match:
    return "not available"
  return " / ".join(match.group(1).strip().splitlines())


def selected_button(player):
  return ButtonSpec(
      player["button"]["name"],
      normalize_recipe(player["button"]["recipe"]),
  )


def player_wins(player):
  return int(player.get("gameScoreArray", {}).get("W", 0))


def main(argv=None):
  args = parse_args(argv)
  if args.matches < 2:
    raise SystemExit("--matches must be at least 2 so both orientations are run")
  if args.workers < 1:
    raise SystemExit("--workers must be at least 1")
  if args.min_sims < 1 or args.max_sims < args.min_sims:
    raise SystemExit("simulation counts must satisfy 1 <= min-sims <= max-sims")
  if (args.round_min_sims < 1
      or args.round_max_sims < args.round_min_sims):
    raise SystemExit(
        "round simulation counts must satisfy "
        "1 <= round-min-sims <= round-max-sims")
  if args.heartbeat < 1:
    raise SystemExit("--heartbeat must be at least 1 second")
  if args.chunk_size < 1:
    raise SystemExit("--chunk-size must be at least 1")

  binary = args.binary or str(resolve_bmair(mode=args.bmair_update))
  client = bmutils.BMClientParser(args.config, args.site)
  if not client.verify_login():
    raise SystemExit("Could not log in")
  game = client.wrap_load_game_data(args.game_id)
  if game["gameState"] != "START_TURN":
    raise SystemExit(
        f"Game {args.game_id} is {game['gameState']}; only START_TURN is supported")

  current_player = game["player"]
  waiting_player = game["opponent"]
  round_request = current_round_request(game, args)
  round_output = run_bmair(
      binary, round_request, timeout=args.timeout)
  current_win_rate = parse_win_percent(round_output) / 100.0

  players = game["playerDataArray"]
  actor = selected_button(players[0])
  obstacle = selected_button(players[1])
  seed = args.seed if args.seed is not None else secrets.randbelow(2_147_483_646) + 1
  current_index = int(game["currentPlayerIdx"])
  current_player0_rate = (
      current_win_rate if current_index == 0 else 1.0 - current_win_rate)
  wins0, wins1 = player_wins(players[0]), player_wins(players[1])
  quick_game_rate = (
      current_player0_rate * match_win_probability(
          0.5, wins0 + 1, wins1, int(game["maxWins"]))
      + (1.0 - current_player0_rate) * match_win_probability(
          0.5, wins0, wins1 + 1, int(game["maxWins"])))
  round_winner = (
      current_player["playerName"] if current_win_rate >= 0.5
      else waiting_player["playerName"])
  quick_winner = (
      players[0]["playerName"] if quick_game_rate >= 0.5
      else players[1]["playerName"])

  print(f"Game {args.game_id}")
  print(
      f"Current round: {round_winner} is favored "
      f"({current_player['playerName']} BMAIR score {current_win_rate:.1%}, "
      f"{waiting_player['playerName']} {1.0 - current_win_rate:.1%})")
  print(f"BMAIR action: {action_summary(round_output)}")
  print(
      f"Quick game forecast: {quick_winner} is favored "
      f"({players[0]['playerName']} {quick_game_rate:.1%}, "
      f"{players[1]['playerName']} {1.0 - quick_game_rate:.1%})")
  print(
      "  Quick forecast uses the live round evaluation and real match score, "
      "then treats any later rounds as 50/50.", flush=True)
  if args.debug:
    print("DEBUG current-round BMAIR request:")
    print(round_request.rstrip())
    print(
        "DEBUG full-match configuration: "
        f"binary={binary} mode=native workers={args.workers} "
        f"ply={args.simulation_ply} min_sims={args.min_sims} "
        f"max_sims={args.max_sims} max_branch={args.max_branch} "
        f"seed={seed} chunk_size={args.chunk_size}")
    print(f"DEBUG player 0: {actor.name}: {actor.recipe}")
    print(f"DEBUG player 1: {obstacle.name}: {obstacle.recipe}", flush=True)
  if args.quick:
    return

  print(
      f"Running {args.matches} complete first-to-{game['maxWins']} matches...",
      flush=True)
  player0_wins, player1_wins, _ = simulate_balanced_matchup(
      binary,
      actor,
      obstacle,
      args.matches,
      target_wins=int(game["maxWins"]),
      seed=seed,
      workers=args.workers,
      ply=args.simulation_ply,
      min_sims=args.min_sims,
      max_sims=args.max_sims,
      max_branch=args.max_branch,
      timeout=args.timeout,
      chunk_size=args.chunk_size,
      heartbeat=lambda elapsed: print(
          f"  BMAIR is still working on the current chunk "
          f"({elapsed:.0f}s elapsed)...", flush=True),
      heartbeat_seconds=args.heartbeat,
      starting=lambda number, total, orientation, chunk_seed, size: print(
          f"  starting match {number}/{total}"
          f"{'-' + str(number + size - 1) if size > 1 else ''}: "
          f"orientation={orientation} seed={chunk_seed}",
          flush=True,
      ),
      progress=lambda done, total, wins0, wins1: print(
          f"  simulated {done}/{total}: "
          f"{players[0]['playerName']} {wins0}, "
          f"{players[1]['playerName']} {wins1}",
          flush=True,
      ),
  )
  player0_baseline = player0_wins / args.matches

  effective_round_rate = infer_round_probability(
      player0_baseline, int(game["maxWins"]))
  if current_player0_rate >= 1.0:
    game_rate = match_win_probability(
        effective_round_rate, wins0 + 1, wins1, int(game["maxWins"]))
  elif current_player0_rate <= 0.0:
    game_rate = match_win_probability(
        effective_round_rate, wins0, wins1 + 1, int(game["maxWins"]))
  else:
    game_rate = (
        current_player0_rate * match_win_probability(
            effective_round_rate, wins0 + 1, wins1, int(game["maxWins"]))
        + (1.0 - current_player0_rate) * match_win_probability(
            effective_round_rate, wins0, wins1 + 1, int(game["maxWins"])))

  game_winner = players[0]["playerName"] if game_rate >= 0.5 else players[1]["playerName"]

  print(
      f"Fresh first-to-{game['maxWins']} simulation: "
      f"{players[0]['playerName']} {player0_baseline:.1%}, "
      f"{players[1]['playerName']} {1.0 - player0_baseline:.1%} "
      f"({args.matches} matches)")
  print(
      f"Approximate current-game forecast: {game_winner} is favored "
      f"({players[0]['playerName']} {game_rate:.1%}, "
      f"{players[1]['playerName']} {1.0 - game_rate:.1%})")
  print(
      "Swing rule: BMAIR full-match simulations keep the round winner's "
      "swing/option choices and let the loser choose again.")
  print(
      "Caveat: BMAIR cannot begin playgame halfway through a live round. The "
      "current-game percentage combines the live round evaluation, current "
      "match score, and the full-match simulation as a score-aware IID-round "
      "approximation; the fresh-match percentage is the raw BMAIR result. "
      "BMAIR scores a drawn round as half a win.")


if __name__ == "__main__":
  main()
