"""Shared helpers for running reproducible Button Men simulations with BMAIR."""

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import subprocess
import time


MATCH_RESULT_RE = re.compile(r"^matches over (\d+) - (\d+)$", re.MULTILINE)
WIN_PERCENT_RE = re.compile(
    r"best move \([^\n]*?,\s*([0-9]+(?:\.[0-9]+)?)% win\)")
DYNAMIC_RECIPE_PREFIX = "@"


@dataclass(frozen=True)
class ButtonSpec:
  name: str
  recipe: str


@dataclass(frozen=True)
class MatchSimulation:
  player0_wins: int
  player1_wins: int
  output: str

  @property
  def games(self):
    return self.player0_wins + self.player1_wins


def read_button_list(path):
  """Read unique ``name: recipe`` entries from a UTF-8 text file."""
  path = Path(path)
  buttons = []
  names = set()
  for line_number, raw_line in enumerate(
      path.read_text(encoding="utf-8").splitlines(), start=1):
    line = raw_line.strip()
    if not line or line.startswith("//") or (
        line.startswith("#") and ":" not in line):
      continue
    if ":" not in line:
      raise ValueError(
          f"{path}:{line_number}: expected 'button name: recipe'")
    name, recipe = (part.strip() for part in line.split(":", 1))
    if not name or not recipe:
      raise ValueError(
          f"{path}:{line_number}: button name and recipe are required")
    folded_name = name.casefold()
    if folded_name in names:
      raise ValueError(f"{path}:{line_number}: duplicate button {name!r}")
    names.add(folded_name)
    buttons.append(ButtonSpec(name, normalize_recipe(recipe)))
  if not buttons:
    raise ValueError(f"{path}: no button entries found")
  return buttons


def normalize_recipe(recipe):
  """Convert site recipe spelling into the notation accepted by BMAIR.

  The site often writes ordinary dice as ``(20)``, skills as ``M(10)``, and
  selected swings as ``(X=12)``. BMAIR expects ``20``, ``M10``, and ``X-12``.
  Twin dice retain their parentheses because their comma is meaningful.
  """
  normalized = recipe.strip().replace("=", "-")
  # Website recipes can place postfix skills before a parenthesized die when
  # that die also has prefix skills: p?(X) means pX?, not p?X.
  normalized = re.sub(r"([?!&]+)\(([^()]+)\)", r"(\2)\1", normalized)
  simple_parentheses = re.compile(r"\(([^(),]+)\)")
  while simple_parentheses.search(normalized):
    normalized = simple_parentheses.sub(r"\1", normalized)
  normalized = re.sub(r"\b([P-Z])-([0-9]+)!", r"\1!-\2", normalized)
  return " ".join(normalized.split())


def recipe_dice(recipe):
  dice = normalize_recipe(recipe).split()
  if not dice:
    raise ValueError("a button recipe must contain at least one die")
  return dice


def materialize_button(button, opponent, seed):
  """Resolve a dynamic site button to its recipe for one simulated match."""
  directive = button.recipe.casefold()
  if not directive.startswith(DYNAMIC_RECIPE_PREFIX):
    return button
  if directive == "@opponent":
    if opponent.recipe.startswith(DYNAMIC_RECIPE_PREFIX):
      raise ValueError("two dynamic copy-opponent recipes cannot be paired")
    return ButtonSpec(button.name, opponent.recipe)

  raise ValueError(f"unknown dynamic recipe directive {button.recipe!r}")


def stable_seed(*parts):
  """Return a stable, nonzero 31-bit seed for one reproducible simulation."""
  digest = hashlib.sha256("\0".join(map(str, parts)).encode("utf-8")).digest()
  return int.from_bytes(digest[:4], "big") % 2_147_483_646 + 1


def build_match_request(player0, player1, games, *, target_wins=3, seed=1,
                        mode="native", workers=4, ply=1, min_sims=5,
                        max_sims=20, max_branch=400):
  """Build a BMAIR request for complete matches from preround."""
  if games < 1:
    raise ValueError("games must be at least 1")
  players = [player0, player1]
  lines = [
      f"mode {mode}",
      f"workers {workers}",
      f"seed {seed}",
      f"ply {ply}",
      f"max_sims {max_sims}",
      f"min_sims {min_sims}",
      f"maxbranch {max_branch}",
      "surrender off",
      f"game {target_wins}",
      "preround",
  ]
  for player_number, button in enumerate(players):
    dice = recipe_dice(button.recipe)
    lines.append(f"player {player_number} {len(dice)} 0")
    lines.extend(dice)
  lines.extend((f"playgame {games}", "quit"))
  return "\n".join(lines) + "\n"


def run_bmair(binary, request, timeout=600, heartbeat=None,
              heartbeat_seconds=30):
  """Run one complete legacy-protocol request and return BMAIR's output."""
  process = subprocess.Popen(
        [str(binary)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
  )
  started = time.monotonic()
  remaining_input = request
  while True:
    elapsed = time.monotonic() - started
    remaining = timeout - elapsed
    if remaining <= 0:
      process.kill()
      process.communicate()
      raise RuntimeError(f"BMAIR did not finish within {timeout} seconds")
    wait_for = min(heartbeat_seconds, remaining)
    try:
      stdout, stderr = process.communicate(
          remaining_input, timeout=wait_for)
      break
    except subprocess.TimeoutExpired:
      remaining_input = None
      if heartbeat:
        heartbeat(time.monotonic() - started)

  output = stdout + stderr
  if process.returncode:
    raise RuntimeError(
        f"BMAIR exited with status {process.returncode}:\n{output.strip()}")
  return output


def parse_match_simulation(output):
  matches = MATCH_RESULT_RE.findall(output)
  if not matches:
    raise ValueError("BMAIR output did not contain a 'matches over' result")
  player0_wins, player1_wins = map(int, matches[-1])
  return MatchSimulation(player0_wins, player1_wins, output)


def simulate_matches(binary, player0, player1, games, **settings):
  timeout = settings.pop("timeout", 600)
  heartbeat = settings.pop("heartbeat", None)
  heartbeat_seconds = settings.pop("heartbeat_seconds", 30)
  request = build_match_request(player0, player1, games, **settings)
  return parse_match_simulation(
      run_bmair(
          binary, request, timeout=timeout, heartbeat=heartbeat,
          heartbeat_seconds=heartbeat_seconds))


def simulate_balanced_matchup(binary, actor, obstacle, games, *, seed=1,
                              timeout=600, chunk_size=50, progress=None,
                              starting=None, **settings):
  """Split matches across both player slots and return actor/obstacle wins."""
  if chunk_size < 1:
    raise ValueError("chunk_size must be at least 1")

  completed_games = 0

  def report_progress(actor_wins, obstacle_wins):
    if progress:
      progress(completed_games, games, actor_wins, obstacle_wins)

  if (actor.recipe.startswith(DYNAMIC_RECIPE_PREFIX)
      or obstacle.recipe.startswith(DYNAMIC_RECIPE_PREFIX)):
    actor_wins = 0
    obstacle_wins = 0
    outputs = []
    for game_number in range(games):
      game_seed = stable_seed(seed, game_number)
      materialized_actor = materialize_button(actor, obstacle, game_seed)
      materialized_obstacle = materialize_button(
          obstacle, materialized_actor, stable_seed(game_seed, "obstacle"))
      if game_number % 2 == 0:
        if starting:
          starting(game_number + 1, games, "forward", game_seed, 1)
        result = simulate_matches(
            binary, materialized_actor, materialized_obstacle, 1,
            seed=game_seed, timeout=timeout, **settings)
        actor_wins += result.player0_wins
        obstacle_wins += result.player1_wins
      else:
        if starting:
          starting(game_number + 1, games, "reverse", game_seed, 1)
        result = simulate_matches(
            binary, materialized_obstacle, materialized_actor, 1,
            seed=game_seed, timeout=timeout, **settings)
        actor_wins += result.player1_wins
        obstacle_wins += result.player0_wins
      outputs.append(result.output)
      completed_games += 1
      if completed_games % chunk_size == 0 or completed_games == games:
        report_progress(actor_wins, obstacle_wins)
    return actor_wins, obstacle_wins, outputs

  forward_games = (games + 1) // 2
  reverse_games = games // 2
  actor_wins = 0
  obstacle_wins = 0
  outputs = []
  for orientation, orientation_games in (
      ("forward", forward_games), ("reverse", reverse_games)):
    orientation_completed = 0
    while orientation_completed < orientation_games:
      games_in_chunk = min(
          chunk_size, orientation_games - orientation_completed)
      chunk_seed = stable_seed(
          seed, orientation, orientation_completed)
      if starting:
        starting(
            completed_games + 1, games, orientation, chunk_seed,
            games_in_chunk)
      if orientation == "forward":
        try:
          result = simulate_matches(
              binary, actor, obstacle, games_in_chunk,
              seed=chunk_seed, timeout=timeout, **settings)
        except RuntimeError as error:
          raise RuntimeError(
              f"BMAIR failed in {orientation} matches "
              f"{completed_games + 1}-{completed_games + games_in_chunk}; "
              f"seed={chunk_seed}; player0={actor.name}:{actor.recipe}; "
              f"player1={obstacle.name}:{obstacle.recipe}: {error}") from error
        actor_wins += result.player0_wins
        obstacle_wins += result.player1_wins
      else:
        try:
          result = simulate_matches(
              binary, obstacle, actor, games_in_chunk,
              seed=chunk_seed, timeout=timeout, **settings)
        except RuntimeError as error:
          raise RuntimeError(
              f"BMAIR failed in {orientation} matches "
              f"{completed_games + 1}-{completed_games + games_in_chunk}; "
              f"seed={chunk_seed}; player0={obstacle.name}:{obstacle.recipe}; "
              f"player1={actor.name}:{actor.recipe}: {error}") from error
        actor_wins += result.player1_wins
        obstacle_wins += result.player0_wins
      outputs.append(result.output)
      orientation_completed += games_in_chunk
      completed_games += games_in_chunk
      report_progress(actor_wins, obstacle_wins)
  return actor_wins, obstacle_wins, outputs


def parse_win_percent(output):
  matches = WIN_PERCENT_RE.findall(output)
  if not matches:
    raise ValueError("BMAIR output did not contain a best-move win percentage")
  return float(matches[-1])


def match_win_probability(round_probability, wins, losses, target_wins=3):
  """Probability of winning a race to target_wins with IID future rounds."""
  if wins >= target_wins:
    return 1.0
  if losses >= target_wins:
    return 0.0
  probability = float(round_probability)
  table = {}

  def visit(player_wins, opponent_wins):
    if player_wins >= target_wins:
      return 1.0
    if opponent_wins >= target_wins:
      return 0.0
    key = (player_wins, opponent_wins)
    if key not in table:
      table[key] = (
          probability * visit(player_wins + 1, opponent_wins)
          + (1.0 - probability) * visit(player_wins, opponent_wins + 1))
    return table[key]

  return visit(wins, losses)


def infer_round_probability(match_probability, target_wins=3):
  """Find the IID round rate producing a given fresh-match win rate."""
  low, high = 0.0, 1.0
  for _ in range(60):
    middle = (low + high) / 2.0
    estimate = match_win_probability(middle, 0, 0, target_wins)
    if estimate < match_probability:
      low = middle
    else:
      high = middle
  return (low + high) / 2.0
