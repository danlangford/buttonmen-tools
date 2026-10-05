#!/usr/bin/env python3
"""Simulate every actor button against every obstacle button with BMAIR."""

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path

from bmair_release import resolve_bmair
from bmair_simulation import read_button_list, simulate_balanced_matchup, stable_seed


def parse_args(argv=None):
  parser = argparse.ArgumentParser(
      description=(
          "Run complete first-to-three BMAIR matches for every actor/obstacle "
          "pairing and export pairing details plus actor rankings."))
  parser.add_argument("actors", help="UTF-8 file containing name: recipe lines")
  parser.add_argument("obstacles", help="UTF-8 file containing name: recipe lines")
  parser.add_argument(
      "-o", "--output", default="matchup-results",
      help="output prefix (writes PREFIX-pairings.csv, PREFIX-actors.csv, and PREFIX.json)")
  parser.add_argument("-b", "--binary")
  parser.add_argument(
      "--bmair-update", choices=("auto", "never", "force"), default="auto")
  parser.add_argument("--matches", type=int, default=100)
  parser.add_argument("--target-wins", type=int, default=3)
  parser.add_argument("--workers", type=int, default=4)
  parser.add_argument("--ply", type=int, default=2)
  parser.add_argument("--min-sims", type=int, default=5)
  parser.add_argument("--max-sims", type=int, default=20)
  parser.add_argument("--max-branch", type=int, default=400)
  parser.add_argument("--timeout", type=int, default=600)
  parser.add_argument(
      "--chunk-size", type=int, default=50,
      help="complete this many matches per BMAIR invocation and progress update")
  parser.add_argument("--heartbeat", type=int, default=30)
  parser.add_argument("--seed", type=int, default=20260902)
  return parser.parse_args(argv)


def actor_summaries(actors, obstacles, pairings):
  summaries = []
  for actor in actors:
    rows = [row for row in pairings if row["actor"] == actor.name]
    rates = [row["actor_win_rate"] for row in rows]
    sweep_probability = math.prod(rates)
    summaries.append({
        "actor": actor.name,
        "recipe": actor.recipe,
        "obstacles": len(obstacles),
        "matches": sum(row["matches"] for row in rows),
        "wins": sum(row["actor_wins"] for row in rows),
        "losses": sum(row["obstacle_wins"] for row in rows),
        "average_win_rate": sum(rates) / len(rates),
        "minimum_win_rate": min(rates),
        "favored_matchups": sum(rate > 0.5 for rate in rates),
        "estimated_sweep_probability": sweep_probability,
    })
  return sorted(
      summaries,
      key=lambda row: (
          row["estimated_sweep_probability"],
          row["minimum_win_rate"],
          row["average_win_rate"],
      ),
      reverse=True,
  )


def write_csv(path, rows, fields):
  path.parent.mkdir(parents=True, exist_ok=True)
  with path.open("w", newline="", encoding="utf-8") as output_file:
    writer = csv.DictWriter(output_file, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)


def main(argv=None):
  args = parse_args(argv)
  if args.matches < 2:
    raise SystemExit("--matches must be at least 2 so both orientations are run")
  if args.target_wins < 1:
    raise SystemExit("--target-wins must be at least 1")
  if args.workers < 1:
    raise SystemExit("--workers must be at least 1")
  if args.min_sims < 1 or args.max_sims < args.min_sims:
    raise SystemExit("simulation counts must satisfy 1 <= min-sims <= max-sims")
  if args.chunk_size < 1:
    raise SystemExit("--chunk-size must be at least 1")
  if args.heartbeat < 1:
    raise SystemExit("--heartbeat must be at least 1 second")

  actors = read_button_list(args.actors)
  obstacles = read_button_list(args.obstacles)
  binary = args.binary or str(resolve_bmair(mode=args.bmair_update))
  prefix = Path(args.output)
  pairings_path = prefix.parent / f"{prefix.name}-pairings.csv"
  actors_path = prefix.parent / f"{prefix.name}-actors.csv"
  json_path = prefix.parent / f"{prefix.name}.json"
  pairings = []
  total = len(actors) * len(obstacles)
  completed = 0
  for actor in actors:
    for obstacle in obstacles:
      completed += 1
      print(
          f"[{completed}/{total}] {actor.name} vs {obstacle.name} "
          f"({args.matches} matches)", flush=True)
      seed = stable_seed(args.seed, actor.name, obstacle.name)
      actor_wins, obstacle_wins, _ = simulate_balanced_matchup(
          binary,
          actor,
          obstacle,
          args.matches,
          target_wins=args.target_wins,
          seed=seed,
          workers=args.workers,
          ply=args.ply,
          min_sims=args.min_sims,
          max_sims=args.max_sims,
          max_branch=args.max_branch,
          timeout=args.timeout,
          chunk_size=args.chunk_size,
          heartbeat=lambda elapsed: print(
              f"  BMAIR is still working on the current chunk "
              f"({elapsed:.0f}s elapsed)...", flush=True),
          heartbeat_seconds=args.heartbeat,
          starting=lambda number, total_matches, orientation, chunk_seed, size: print(
              f"  starting matches {number}-"
              f"{number + size - 1}/{total_matches}: "
              f"orientation={orientation} seed={chunk_seed}",
              flush=True,
          ),
          progress=lambda done, total_matches, wins, losses: print(
              f"  {done}/{total_matches} complete: "
              f"{actor.name} {wins}, {obstacle.name} {losses}",
              flush=True,
          ),
      )
      pairings.append({
          "actor": actor.name,
          "actor_recipe": actor.recipe,
          "obstacle": obstacle.name,
          "obstacle_recipe": obstacle.recipe,
          "matches": actor_wins + obstacle_wins,
          "actor_wins": actor_wins,
          "obstacle_wins": obstacle_wins,
          "actor_win_rate": actor_wins / (actor_wins + obstacle_wins),
      })
      # A large field can run for hours. Preserve every completed pairing so a
      # later timeout or interruption does not erase earlier measurements.
      write_csv(pairings_path, pairings, list(pairings[0]))

  summaries = actor_summaries(actors, obstacles, pairings)
  write_csv(actors_path, summaries, list(summaries[0]))
  report = {
      "generated_at": datetime.now(timezone.utc).isoformat(),
      "engine": str(binary),
      "settings": {
          "matches_per_pairing": args.matches,
          "target_wins": args.target_wins,
          "workers": args.workers,
          "ply": args.ply,
          "min_sims": args.min_sims,
          "max_sims": args.max_sims,
          "max_branch": args.max_branch,
          "chunk_size": args.chunk_size,
          "heartbeat_seconds": args.heartbeat,
          "root_seed": args.seed,
          "orientation": "split as evenly as possible across player slots",
          "swing_rule": (
              "BMAIR retains the round winner's swing/option selections and "
              "unlocks the loser's selections"),
      },
      "actors": [asdict(actor) for actor in actors],
      "obstacles": [asdict(obstacle) for obstacle in obstacles],
      "pairings": pairings,
      "actor_rankings": summaries,
  }
  json_path.parent.mkdir(parents=True, exist_ok=True)
  with json_path.open("w", encoding="utf-8") as output_file:
    json.dump(report, output_file, indent=2)
    output_file.write("\n")

  winner = summaries[0]
  print()
  print(
      f"Top actor: {winner['actor']} — average "
      f"{winner['average_win_rate']:.1%}, weakest matchup "
      f"{winner['minimum_win_rate']:.1%}, estimated chance of sweeping every "
      f"obstacle {winner['estimated_sweep_probability']:.1%}")
  print(f"Pairings: {pairings_path}")
  print(f"Actor rankings: {actors_path}")
  print(f"Complete report: {json_path}")


if __name__ == "__main__":
  main()
