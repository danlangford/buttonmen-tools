#!/usr/bin/env python3
"""Watch the OPERATION LOOKING GLASS forum thread for new volunteers.

The monitor polls the complete thread.  When a player posts some form of
"I accept" and names a button, it starts fight 1/6 and creates a new forum
post that becomes the adventure's log.

This file is intentionally a little more explicit than compact: the checks
which prevent duplicate games are meant to be easy to inspect and change.
"""

import argparse
import fcntl
import json
import logging
import os
import re
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

import bmutils


LOGGER = logging.getLogger("watchwonderland")

THREAD_ID = 1368
LEADERBOARD_POST_ID = 33723
LEADERBOARD_FORMAT_MARKER = "[code]LOOKING GLASS LEADERBOARD | format=2[/code]"
BOT_PLAYER = "BMAIBagels"
OPERATION_NAME = "OPERATION LOOKING GLASS"
WONDERLAND_OPPONENTS = (
    "Tweedledum+dee",
    "Mad Hatter",
    "White Rabbit",
    "Queen Of Hearts",
    "The Jabberwock",
    "Alice",
)
PENDING_FILE = Path(".watchwonderland.pending.json")
LOCK_FILE = Path(".watchwonderland.lock")
BUTTONMEN_TOOLS_DIR = Path(__file__).resolve().parent.parent
BUTTON_FILTER_URL = (
    "http://buttonmen.s3-website-us-east-1.amazonaws.com/ButtonFilter.html")
MAXIMUM_WIN_RATE = 60.0
OPERATION_SUPPORTED_BUTTON_EXCEPTIONS = {"Echo"}

# Tags may be quoted or unquoted because forum users commonly type both.
BUTTON_TAG_RE = re.compile(
    r'\[button\s*=\s*(?:"([^"\]\n]+)"|\'([^\'\]\n]+)\'|([^\]"\'\n]+))\]',
    re.IGNORECASE,
)
ACCEPT_RE = re.compile(
    r"^\s*i\s+accept\b", re.IGNORECASE)
PLAIN_ACCEPT_RE = re.compile(
    r"^\s*i\s+accept\b\s*(.*?)\s*$",
    re.IGNORECASE,
)
MISSION_HEADER_RE = re.compile(
    r"^\[MISSION:\s*(.+?)\s+-\s+(.+?)\]\s*$", re.IGNORECASE)
QUEST_HEADER_RE = re.compile(
    r"OPERATION LOOKING GLASS\s*\|\s*"
    r"source-post=(\d+)\s*\|\s*"
    r"player=([^|\n]+?)\s*\|\s*"
    r"button=([^|\n]+?)\s*\|\s*"
    r"status=(creating|active|failed|survived)",
    re.IGNORECASE,
)
FIGHT_RECORD_RE = re.compile(
    r"LOOKING GLASS FIGHT\s*\|\s*"
    r"number=(\d+)\s*\|\s*"
    r"game=(\d+)\s*\|\s*"
    r"opponent=([^|\n]+?)\s*\|\s*"
    r"result=(active|won|lost)",
    re.IGNORECASE,
)
REJECTION_RECORD_RE = re.compile(
    r"OPERATION LOOKING GLASS\s*\|\s*"
    r"source-post=(\d+)\s*\|\s*decision=rejected",
    re.IGNORECASE,
)
LEGACY_QUEST_RE = re.compile(
    r"OPERATION LOOKING GLASS\s*\|\s*"
    r"source-post=(\d+)\s*\|\s*"
    r"player=([^|\n]+?)\s*\|\s*"
    r"button=([^|\n]+?)\s*\|\s*"
    r"game=(\d+)\s*\|\s*"
    r"status=(active|complete)",
    re.IGNORECASE,
)
ROUND_WIN_RE = re.compile(
    r"^End of round:\s+(.+?)\s+won round\s+\d+\s+"
    r"\((-?\d+(?:\.\d+)?)\s+vs\.\s+(-?\d+(?:\.\d+)?)\)$",
    re.IGNORECASE,
)
ROUND_DRAW_RE = re.compile(
    r"^Round\s+\d+\s+ended in a draw\s+"
    r"\((-?\d+(?:\.\d+)?)\s+vs\.\s+(-?\d+(?:\.\d+)?)\)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Acceptance:
  """A player's request to begin an adventure."""

  post_id: int
  player: str
  button: str


@dataclass(frozen=True)
class FightLog:
  """One game recorded in a quest's forum post."""

  number: int
  game_id: int
  opponent: str
  result: str


@dataclass(frozen=True)
class QuestLog:
  """A complete quest reconstructed from its edited forum post."""

  forum_post_id: int
  source_post_id: int
  player: str
  button: str
  status: str
  fights: tuple[FightLog, ...]

  @property
  def current_fight(self):
    return self.fights[-1]


@dataclass(frozen=True)
class ButtonEligibility:
  """The Button Filter facts needed to accept or reject one button."""

  name: str
  bmaibagels_can_read: bool
  win_rate: float | None


@dataclass(frozen=True)
class LeaderboardEntry:
  player: str
  button: str
  opponents_defeated: int
  rounds_lost: int
  points_scored: float
  status: str
  defeated_by: str | None = None


class ButtonFilterPublication:
  """Read the current Button Filter publication from buttonmen-tools.

  The capability lists remain owned by ButtonFilter.html.  We read them from
  there instead of maintaining a second copy which could drift out of date.
  """

  CAPABILITY_NAMES = (
      "bmaibagels_supported_button_name",
      "bmaibagels_unsupported_button_name",
      "bmaibagels_unsupported_button_set",
      "bmaibagels_supported_die_features",
  )

  def __init__(self, tools_directory=BUTTONMEN_TOOLS_DIR):
    self.public_directory = Path(tools_directory) / "public"
    self.reload()

  def reload(self):
    """Reload the files so each poll uses the current publication."""
    self.button_data = self._read_json(
        self.public_directory / "buttondata.json")
    self.button_stats = self._read_json(
        self.public_directory / "buttonstats.json")
    self.filter_html = (self.public_directory / "ButtonFilter.html").read_text(
        encoding="utf-8")
    self.capabilities = {
        name: set(self._read_javascript_array(name))
        for name in self.CAPABILITY_NAMES
    }

    self.buttons_by_case = {
        button["buttonName"].casefold(): button
        for button in self.button_data["data"]
    }
    self.stats_by_case = {
        name.casefold(): stats
        for name, stats in self.button_stats["data"].items()
    }

  @staticmethod
  def _read_json(path):
    with path.open(encoding="utf-8") as input_file:
      return json.load(input_file)

  def _read_javascript_array(self, variable_name):
    pattern = re.compile(
        rf"\b{re.escape(variable_name)}\s*=\s*(\[[\s\S]*?\])")
    match = pattern.search(self.filter_html)
    if not match:
      raise ValueError(
          f"Could not find {variable_name} in the Button Filter publication")
    return json.loads(match.group(1))

  def eligibility(self, button_name):
    """Return the published BMAIBagels support and win rate for a button."""
    button = self.buttons_by_case.get(button_name.casefold())
    if button is None:
      return None

    name = button["buttonName"]
    supported_names = self.capabilities["bmaibagels_supported_button_name"]
    unsupported_names = self.capabilities["bmaibagels_unsupported_button_name"]
    unsupported_sets = self.capabilities["bmaibagels_unsupported_button_set"]
    supported_features = self.capabilities["bmaibagels_supported_die_features"]

    features = set(button["dieSkills"] + button["dieTypes"])
    can_read = name in supported_names or (
        name not in unsupported_names
        and button["buttonSet"] not in unsupported_sets
        and features <= supported_features
    )
    stats = self.stats_by_case.get(name.casefold())
    win_rate = float(stats["rate"]) if stats is not None else None
    return ButtonEligibility(name, can_read, win_rate)


def parse_acceptance(post):
  """Return an Acceptance when a non-bot post contains the required text."""
  if post.get("deleted") or post.get("posterName", "").casefold() == BOT_PLAYER.casefold():
    return None

  body = post.get("body", "")
  if not ACCEPT_RE.search(body):
    return None

  button_match = BUTTON_TAG_RE.search(body)
  if button_match:
    button = next(
        value for value in button_match.groups() if value is not None)
  else:
    plain_match = PLAIN_ACCEPT_RE.search(body)
    button = plain_match.group(1).strip() if plain_match else ""
    if (len(button) >= 2 and button[0] == button[-1]
        and button[0] in ('"', "'")):
      button = button[1:-1].strip()
    # A malformed tag is not a plain button name. It will receive format help.
    if button.startswith("["):
      button = ""

  return Acceptance(
      post_id=int(post["postId"]),
      player=post["posterName"].strip(),
      button=button.strip(),
  )


def parse_adventure_logs(posts):
  """Find quest records, including the format used by the first monitor.

  If more than one log exists for an acceptance, the earliest forum post is
  authoritative. This is how the thread resolves an accidental duplicate.
  """
  logs = []
  for post in posts:
    if post.get("deleted"):
      continue
    if post.get("posterName", "").casefold() != BOT_PLAYER.casefold():
      continue
    body = post.get("body", "")
    mission_header = MISSION_HEADER_RE.match(body.split("\n", 1)[0])
    header = QUEST_HEADER_RE.search(body)
    if not header:
      legacy = LEGACY_QUEST_RE.search(body)
      if legacy:
        legacy_status = legacy.group(5).lower()
        logs.append(QuestLog(
            forum_post_id=int(post["postId"]),
            source_post_id=int(legacy.group(1)),
            player=legacy.group(2).strip(),
            button=legacy.group(3).strip(),
            status="active" if legacy_status == "active" else "failed",
            fights=(FightLog(
                1, int(legacy.group(4)), WONDERLAND_OPPONENTS[0],
                "active" if legacy_status == "active" else "lost"),),
        ))
      continue
    fights = tuple(
        FightLog(
            number=int(match.group(1)),
            game_id=int(match.group(2)),
            opponent=match.group(3).strip(),
            result=match.group(4).lower(),
        )
        for match in FIGHT_RECORD_RE.finditer(body)
    )
    status = header.group(4).lower()
    if not fights and status != "creating":
      continue
    logs.append(QuestLog(
        forum_post_id=int(post["postId"]),
        source_post_id=int(header.group(1)),
        player=(mission_header.group(1).strip() if mission_header
                else header.group(2).strip()),
        button=(mission_header.group(2).strip() if mission_header
                else header.group(3).strip()),
        status=status,
        fights=fights,
    ))

  authoritative_logs = {}
  for log in sorted(logs, key=lambda item: item.forum_post_id):
    authoritative_logs.setdefault(log.source_post_id, log)
  return list(authoritative_logs.values())


def parse_rejected_source_posts(posts):
  """Find acceptance posts for which we have already posted a rejection."""
  rejected_source_posts = set()
  for post in posts:
    if post.get("deleted"):
      continue
    if post.get("posterName", "").casefold() != BOT_PLAYER.casefold():
      continue
    rejected_source_posts.update(
        int(match.group(1))
        for match in REJECTION_RECORD_RE.finditer(post.get("body", ""))
    )
  return rejected_source_posts


def acceptance_has_been_handled(acceptance, logs, rejected_source_posts):
  return (acceptance.post_id in rejected_source_posts or
          any(log.source_post_id == acceptance.post_id for log in logs))


def rejection_reason(acceptance, logs):
  """Explain which existing quest constraint an acceptance violates."""
  previous_player_button_quest = next(
      (log for log in logs
       if log.player.casefold() == acceptance.player.casefold()
       and log.button.casefold() == acceptance.button.casefold()),
      None,
  )
  if previous_player_button_quest:
    return (
        f"{acceptance.player} has already attempted Wonderland with "
        f"{previous_player_button_quest.button}; each player may attempt "
        "a given button only once"
    )

  return None


def eligibility_rejection_reason(eligibility):
  """Return the published Button Filter reason a button is ineligible."""
  operation_can_read = (
      eligibility.bmaibagels_can_read
      or eligibility.name in OPERATION_SUPPORTED_BUTTON_EXCEPTIONS
  )
  if not operation_can_read:
    return f"BMAIBagels cannot read {eligibility.name}"
  if eligibility.win_rate is None:
    return f"{eligibility.name} has no published win rate"
  if eligibility.win_rate >= MAXIMUM_WIN_RATE:
    return (
        f"{eligibility.name} has a published win rate of "
        f"{eligibility.win_rate:g}%; OPERATION LOOKING GLASS requires a "
        f"win rate below {MAXIMUM_WIN_RATE:g}%"
    )
  return None


def make_rejection_post(acceptance, reason):
  """Build a forum post which rejects and permanently records an assignment."""
  record = (
      f"{OPERATION_NAME} | source-post={acceptance.post_id} | "
      "decision=rejected"
  )
  return (
      f"{acceptance.player}, this assignment cannot be accepted.\n\n"
      f"{reason}.\n\n"
      f"Acceptance: [forum={THREAD_ID},{acceptance.post_id}]"
      "source post[/forum]\n\n"
      "Please use one of these formats with an exact published button name:\n"
      "[code]I accept [button=Aylee]\n"
      "I accept Aylee[/code]\n\n"
      "Select a champion using the current Button Filter publication.\n\n"
      f"{BUTTON_FILTER_URL}\n\n"
      f"[code]{record}[/code]"
  )


def make_quest_log(acceptance, game_id):
  """Create the initial quest state after fight 1 has been created."""
  return QuestLog(
      forum_post_id=0,
      source_post_id=acceptance.post_id,
      player=acceptance.player,
      button=acceptance.button,
      status="active",
      fights=(FightLog(1, game_id, WONDERLAND_OPPONENTS[0], "active"),),
  )


def make_quest_claim(acceptance):
  """Reserve an accepted player/button in the thread before game creation."""
  return QuestLog(
      forum_post_id=0,
      source_post_id=acceptance.post_id,
      player=acceptance.player,
      button=acceptance.button,
      status="creating",
      fights=(),
  )


def render_quest_post(quest):
  """Render both the human journey log and its machine-readable state."""
  journey_lines = []
  record_lines = [
      f"{OPERATION_NAME} | source-post={quest.source_post_id} | "
      f"player={quest.player} | button={quest.button} | status={quest.status}"
  ]
  for fight in quest.fights:
    result = "IN PROGRESS" if fight.result == "active" else fight.result.upper()
    journey_lines.append(
        f"Fight {fight.number}/6 — {fight.opponent}: "
        f"[game={fight.game_id}] — {result}"
    )
    record_lines.append(
        f"LOOKING GLASS FIGHT | number={fight.number} | game={fight.game_id} | "
        f"opponent={fight.opponent} | result={fight.result}"
    )

  if quest.status == "creating":
    conclusion = "Assignment accepted. Preparing fight 1/6."
  elif quest.status == "active":
    conclusion = "The quest continues."
  elif quest.status == "survived":
    conclusion = (
        "The champion defeated Alice and survived OPERATION LOOKING GLASS."
    )
  else:
    conclusion = (
        "The champion was lost in Wonderland. This quest has ended in failure."
    )

  return (
      f"[MISSION: {quest.player} - {quest.button}]\n"
      f"{quest.player}'s OPERATION LOOKING GLASS journey\n\n"
      f"Champion: {quest.button}\n"
      f"Accepted from: [forum={THREAD_ID},{quest.source_post_id}]"
      "source post[/forum]\n\n"
      + "\n".join(journey_lines)
      + f"\n\n{conclusion}\n\n"
      + "[code]" + "\n".join(record_lines) + "[/code]"
  )


def game_description(fight_number):
  return f"{OPERATION_NAME} FIGHT {fight_number}/6"


def player_data(game, player_name):
  """Find one player's data in a loadGameData response."""
  return next(
      player for player in game["playerDataArray"]
      if player["playerName"].casefold() == player_name.casefold()
  )


def points_scored_in_game(game, player_name):
  """Sum the player's final scores from every scored round in a game.

  Surrendered rounds contain no score and contribute zero. Completed game
  responses include the full action log even though active-game responses are
  normally truncated.
  """
  total = 0.0
  for entry in game.get("gameActionLog", []):
    message = entry.get("message", "")
    win = ROUND_WIN_RE.match(message)
    if win:
      winner, winning_score, losing_score = win.groups()
      score = winning_score if winner.casefold() == player_name.casefold() else losing_score
      total += float(score)
      continue
    draw = ROUND_DRAW_RE.match(message)
    if draw:
      total += float(draw.group(1))
  return total


def build_leaderboard(logs, load_game_data):
  """Calculate entries for quests which have failed or survived."""
  entries = []
  for quest in logs:
    if quest.status not in ("failed", "survived"):
      continue
    rounds_lost = 0
    points_scored = 0.0
    for fight in quest.fights:
      if fight.result == "active":
        continue
      game = load_game_data(fight.game_id)
      rounds_lost += int(player_data(
          game, quest.player)["gameScoreArray"]["L"])
      points_scored += points_scored_in_game(game, quest.player)
    entries.append(LeaderboardEntry(
        player=quest.player,
        button=quest.button,
        opponents_defeated=sum(
            fight.result == "won" for fight in quest.fights),
        rounds_lost=rounds_lost,
        points_scored=points_scored,
        status=quest.status,
        defeated_by=next(
            (fight.opponent for fight in quest.fights
             if fight.result == "lost"),
            None,
        ),
    ))

  return sorted(
      entries,
      key=lambda entry: (
          -entry.opponents_defeated,
          entry.rounds_lost,
          -entry.points_scored,
          entry.player.casefold(),
          entry.button.casefold(),
      ),
  )


def leaderboard_entry_key(entry):
  """Return the stable player/button identity used by the leaderboard."""
  return entry.player.casefold(), entry.button.casefold()


def leaderboard_entry_keys(body):
  """Read player/button identities from old and current leaderboard formats."""
  keys = set()
  entry_re = re.compile(
      r"^\d+\.\s+(.+?)\s+—\s+(?:\[button=([^\]]+)\]|(.+?))\s+—\s+"
      r"\d+\s+defeated;",
      re.IGNORECASE,
  )
  for raw_line in body.splitlines():
    line = raw_line.strip()
    if line.startswith("[b]") and line.endswith("[/b]"):
      line = line[3:-4]
    match = entry_re.match(line)
    if match:
      player, tagged_button, plain_button = match.groups()
      keys.add((player.casefold(), (tagged_button or plain_button).casefold()))
  return keys


def render_leaderboard(entries, previous_body=None, bold_all=False):
  """Render the community leaderboard for its reserved forum post."""
  lines = [
      "[b]OPERATION LOOKING GLASS — COMMUNITY LEADERBOARD[/b]",
      "",
      "Ranked by opponents defeated, then fewest rounds lost, then most "
      "points scored.",
      "",
  ]
  if not entries:
    lines.append("No missions have begun.")
    lines.extend(("", LEADERBOARD_FORMAT_MARKER))
    return "\n".join(lines)

  previous_score = None
  previous_outcome = None
  previous_entries = leaderboard_entry_keys(previous_body or "")
  rank = 0
  for position, entry in enumerate(entries, start=1):
    score = (
        entry.opponents_defeated, entry.rounds_lost, entry.points_scored)
    if score != previous_score:
      rank = position
      previous_score = score
    points = f"{entry.points_scored:g}"
    outcome = (
        f"DEFEATED by {entry.defeated_by}"
        if entry.status == "failed" and entry.defeated_by
        else entry.status.upper()
    )
    if previous_outcome is not None and outcome != previous_outcome:
      lines.append("")
    previous_outcome = outcome
    line = (
        f"{rank}. {entry.player} — {entry.button} — "
        f"{entry.opponents_defeated} defeated; "
        f"{entry.rounds_lost} rounds lost; {points} points — "
        f"{outcome}"
    )
    if bold_all or (previous_body is not None and
                    leaderboard_entry_key(entry) not in previous_entries):
      line = f"[b]{line}[/b]"
    lines.append(line)
  lines.extend(("", LEADERBOARD_FORMAT_MARKER))
  return "\n".join(lines)


def load_pending():
  """Load a game awaiting its forum post after an interrupted run."""
  if not PENDING_FILE.exists():
    return None
  with PENDING_FILE.open(encoding="utf-8") as pending_file:
    return json.load(pending_file)


def save_pending_forum_action(action, body, source_post_id, forum_post_id=None):
  """Record the forum write required after creating a game.

  If the website or process fails between those two operations, the next poll
  can finish the forum write without creating a duplicate game.
  """
  pending = {
      "action": action,
      "body": body,
      "source_post_id": source_post_id,
      "forum_post_id": forum_post_id,
  }
  temporary_file = PENDING_FILE.with_suffix(".tmp")
  with temporary_file.open("w", encoding="utf-8") as pending_file:
    json.dump(pending, pending_file, indent=2)
    pending_file.write("\n")
  os.replace(temporary_file, PENDING_FILE)


def clear_pending():
  if PENDING_FILE.exists():
    PENDING_FILE.unlink()


class LookingGlassMonitor:
  """Poll thread 1368 and start eligible OPERATION LOOKING GLASS adventures."""

  def __init__(self, client, button_filter, sleep_seconds=120):
    self.client = client
    self.button_filter = button_filter
    self.sleep_seconds = sleep_seconds

  def restore_pending_forum_action(self, posts):
    """Finish a create/edit interrupted immediately after game creation."""
    pending = load_pending()
    if not pending:
      return False

    if pending["action"] == "create":
      existing_sources = {
          log.source_post_id for log in parse_adventure_logs(posts)
      }
      if int(pending["source_post_id"]) not in existing_sources:
        self.client.wrap_create_forum_post(THREAD_ID, pending["body"])
    elif pending["action"] == "edit":
      forum_post_id = int(pending["forum_post_id"])
      existing_post = next(
          (post for post in posts if int(post["postId"]) == forum_post_id),
          None,
      )
      if existing_post is None or existing_post.get("body") != pending["body"]:
        self.client.wrap_edit_forum_post(forum_post_id, pending["body"])
    else:
      raise ValueError(f"Unknown pending forum action: {pending['action']}")

    clear_pending()
    LOGGER.info("Recovered and finished a pending quest-log forum action")
    return True

  def create_fight(self, quest, fight_number, available_games):
    """Adopt an exact existing game, or create one when none exists."""
    opponent = WONDERLAND_OPPONENTS[fight_number - 1]
    matching_games = [
        game for game in available_games
        if game["opponentName"].casefold() == quest.player.casefold()
        and game["myButtonName"].casefold() == opponent.casefold()
        and game["opponentButtonName"].casefold() == quest.button.casefold()
    ]
    if matching_games:
      game_id = min(int(game["gameId"]) for game in matching_games)
      LOGGER.warning(
          "Adopting existing game instead of creating a duplicate: "
          "player=%s champion=%s fight=%s game=%s",
          quest.player, quest.button, fight_number, game_id)
      return FightLog(fight_number, game_id, opponent, "active")

    game_data = self.client.wrap_create_game(
        opponent,
        quest.button,
        BOT_PLAYER,
        quest.player,
        game_description(fight_number),
    )
    return FightLog(fight_number, int(game_data["gameId"]), opponent, "active")

  @staticmethod
  def quest_player_won(game, player_name):
    """Determine the winner from the final per-player game scores."""
    players = game["playerDataArray"]
    quest_player = next(
        player for player in players
        if player["playerName"].casefold() == player_name.casefold()
    )
    opponent = next(player for player in players if player is not quest_player)
    return (int(quest_player["gameScoreArray"]["W"]) >
            int(opponent["gameScoreArray"]["W"]))

  def reconcile_quest(self, quest, available_games):
    """Advance or finish one quest when its current game has resolved."""
    if quest.status == "creating":
      first_fight = self.create_fight(quest, 1, available_games)
      updated_quest = replace(
          quest, status="active", fights=(first_fight,))
      updated_body = render_quest_post(updated_quest)
      save_pending_forum_action(
          "edit", updated_body, quest.source_post_id, quest.forum_post_id)
      self.client.wrap_edit_forum_post(quest.forum_post_id, updated_body)
      clear_pending()
      LOGGER.info(
          "Quest started: player=%s champion=%s fight=1/6 game=%s opponent=%s",
          quest.player, quest.button, first_fight.game_id,
          first_fight.opponent)
      return updated_quest

    current_fight = quest.current_fight
    active_game_ids = {int(game["gameId"]) for game in available_games}
    if current_fight.game_id in active_game_ids:
      return quest

    # Games disappear from loadActiveGames when they finish. Load the full
    # game to confirm the terminal state and determine who won.
    game = self.client.wrap_load_game_data(current_fight.game_id)
    game_state = game["gameState"]
    if game_state not in ("END_GAME", "CANCELLED"):
      return quest

    won = (game_state == "END_GAME" and
           self.quest_player_won(game, quest.player))
    resolved_fight = replace(
        current_fight, result="won" if won else "lost")
    resolved_fights = quest.fights[:-1] + (resolved_fight,)

    if not won:
      updated_quest = replace(
          quest, status="failed", fights=resolved_fights)
      self.client.wrap_edit_forum_post(
          quest.forum_post_id, render_quest_post(updated_quest))
      LOGGER.info(
          "Quest failed: player=%s champion=%s fight=%s game=%s opponent=%s",
          quest.player, quest.button, current_fight.number,
          current_fight.game_id, current_fight.opponent)
      return updated_quest

    if current_fight.number == len(WONDERLAND_OPPONENTS):
      updated_quest = replace(
          quest, status="survived", fights=resolved_fights)
      self.client.wrap_edit_forum_post(
          quest.forum_post_id, render_quest_post(updated_quest))
      LOGGER.info(
          "Quest survived: player=%s champion=%s Alice_game=%s",
          quest.player, quest.button, current_fight.game_id)
      return updated_quest

    next_fight = self.create_fight(
        quest, current_fight.number + 1, available_games)
    updated_quest = replace(
        quest, fights=resolved_fights + (next_fight,))
    updated_body = render_quest_post(updated_quest)
    save_pending_forum_action(
        "edit", updated_body, quest.source_post_id, quest.forum_post_id)
    self.client.wrap_edit_forum_post(quest.forum_post_id, updated_body)
    clear_pending()
    LOGGER.info(
        "Quest advanced: player=%s champion=%s won_game=%s; "
        "created_fight=%s/6 game=%s opponent=%s",
        quest.player, quest.button, current_fight.game_id,
        next_fight.number, next_fight.game_id, next_fight.opponent)
    return updated_quest

  def check_once(self):
    """Scan once while excluding other local monitor processes."""
    with LOCK_FILE.open("a+", encoding="utf-8") as lock_file:
      fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
      return self._check_once_locked()

  def _check_once_locked(self):
    """Scan the thread and games with the local process lock held."""
    self.button_filter.reload()
    thread = self.client.wrap_load_forum_thread(THREAD_ID)
    posts = thread["posts"]
    if self.restore_pending_forum_action(posts):
      thread = self.client.wrap_load_forum_thread(THREAD_ID)
      posts = thread["posts"]

    available_games = self.client.wrap_load_active_games()
    available_games.extend(self.client.wrap_load_new_games())
    logs = [
        self.reconcile_quest(log, available_games)
        if log.status in ("creating", "active") else log
        for log in parse_adventure_logs(posts)
    ]
    rejected_source_posts = parse_rejected_source_posts(posts)

    for post in posts:
      acceptance = parse_acceptance(post)
      if not acceptance:
        continue

      if acceptance_has_been_handled(
          acceptance, logs, rejected_source_posts):
        LOGGER.debug("Post %s has already been handled", acceptance.post_id)
        continue

      rejection = rejection_reason(acceptance, logs)
      if rejection:
        self.client.wrap_create_forum_post(
            THREAD_ID, make_rejection_post(acceptance, rejection))
        rejected_source_posts.add(acceptance.post_id)
        LOGGER.info(
            "Assignment rejected: post=%s player=%s button=%s reason=%s",
            acceptance.post_id, acceptance.player, acceptance.button, rejection)
        continue

      eligibility = self.button_filter.eligibility(acceptance.button)
      if eligibility is None:
        if acceptance.button:
          rejection = (
              f"{acceptance.button!r} is not an exact published production "
              "button name")
        else:
          rejection = "I could not find a button name in that acceptance"
        self.client.wrap_create_forum_post(
            THREAD_ID, make_rejection_post(acceptance, rejection))
        rejected_source_posts.add(acceptance.post_id)
        LOGGER.info(
            "Assignment rejected: post=%s player=%s button=%s reason=%s",
            acceptance.post_id, acceptance.player, acceptance.button, rejection)
        continue
      acceptance = Acceptance(
          acceptance.post_id, acceptance.player, eligibility.name)

      rejection = eligibility_rejection_reason(eligibility)
      if rejection:
        self.client.wrap_create_forum_post(
            THREAD_ID, make_rejection_post(acceptance, rejection))
        rejected_source_posts.add(acceptance.post_id)
        LOGGER.info(
            "Assignment rejected: post=%s player=%s button=%s reason=%s",
            acceptance.post_id, acceptance.player, acceptance.button, rejection)
        continue

      # Re-read the thread immediately before claiming the assignment. This
      # closes the normal race with another monitor which handled it while the
      # Button Filter checks above were running.
      fresh_posts = self.client.wrap_load_forum_thread(THREAD_ID)["posts"]
      fresh_logs = parse_adventure_logs(fresh_posts)
      fresh_rejections = parse_rejected_source_posts(fresh_posts)
      if acceptance_has_been_handled(
          acceptance, fresh_logs, fresh_rejections):
        continue
      rejection = rejection_reason(acceptance, fresh_logs)
      if rejection:
        self.client.wrap_create_forum_post(
            THREAD_ID, make_rejection_post(acceptance, rejection))
        LOGGER.info(
            "Assignment rejected after final thread check: "
            "post=%s player=%s button=%s reason=%s",
            acceptance.post_id, acceptance.player, acceptance.button, rejection)
        continue

      # The thread claim is persistent storage and happens before createGame.
      # A crash can leave status=creating, which the next scan safely resumes.
      quest = make_quest_claim(acceptance)
      created_thread = self.client.wrap_create_forum_post(
          THREAD_ID, render_quest_post(quest))

      created_log = next(
          log for log in parse_adventure_logs(created_thread["posts"])
          if log.source_post_id == acceptance.post_id
      )
      quest = created_log
      logs.append(quest)

      available_games = self.client.wrap_load_active_games()
      available_games.extend(self.client.wrap_load_new_games())
      quest = self.reconcile_quest(quest, available_games)
      logs[-1] = quest

    self.update_leaderboard(logs, posts)

  def update_leaderboard(self, logs, posts):
    """Edit the reserved leaderboard post only when its contents change."""
    leaderboard_post = next(
        post for post in posts if int(post["postId"]) == LEADERBOARD_POST_ID)
    entries = build_leaderboard(logs, self.client.wrap_load_game_data)
    previous_body = leaderboard_post.get("body", "")
    current_entries = {leaderboard_entry_key(entry) for entry in entries}
    if (LEADERBOARD_FORMAT_MARKER in previous_body and
        leaderboard_entry_keys(previous_body) == current_entries):
      return
    body = render_leaderboard(entries, previous_body=previous_body)
    self.client.wrap_edit_forum_post(LEADERBOARD_POST_ID, body)
    LOGGER.info("Community leaderboard updated with %s missions", len(entries))

  def run_forever(self):
    if not self.client.verify_login():
      raise RuntimeError("Could not log in with the configured account")

    LOGGER.info("Watching forum thread %s every %s seconds",
                THREAD_ID, self.sleep_seconds)
    last_error = None
    while True:
      try:
        self.check_once()
        if last_error is not None:
          LOGGER.info("Monitor recovered after error: %s", last_error)
        last_error = None
      except Exception as error:  # Keep a long-running monitor alive after site errors.
        error_message = str(error)
        if error_message != last_error:
          LOGGER.exception("Monitor check failed")
        else:
          LOGGER.debug("Monitor check failed again: %s", error_message)
        last_error = error_message
      LOGGER.debug("Sleeping at %s",
                   datetime.now().isoformat(timespec="seconds"))
      time.sleep(self.sleep_seconds)


def parse_args():
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument(
      "-c", "--config", default=".bmrc",
      help="configuration file containing the Button Weavers login")
  parser.add_argument(
      "-s", "--site", default="bmaibagels",
      help="section in the configuration file (default: bmaibagels)")
  parser.add_argument(
      "--sleep-seconds", type=int, default=120,
      help="seconds between complete thread scans (default: 120)")
  parser.add_argument(
      "--buttonmen-tools", type=Path, default=BUTTONMEN_TOOLS_DIR,
      help="path to the buttonmen-tools publication")
  parser.add_argument(
      "--log-file", type=Path,
      help="also append monitor events to this file")
  parser.add_argument(
      "--debug", action="store_true",
      help="include polling and already-handled details in the log")
  return parser.parse_args()


def configure_logging(log_file=None, debug=False):
  handlers = [logging.StreamHandler()]
  if log_file is not None:
    handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
  logging.basicConfig(
      level=logging.DEBUG if debug else logging.INFO,
      format="%(asctime)s %(levelname)s %(name)s: %(message)s",
      handlers=handlers,
  )


def main():
  args = parse_args()
  configure_logging(args.log_file, args.debug)
  client = bmutils.BMClientParser(args.config, args.site)
  button_filter = ButtonFilterPublication(args.buttonmen_tools)
  LookingGlassMonitor(client, button_filter, args.sleep_seconds).run_forever()


if __name__ == "__main__":
  main()
