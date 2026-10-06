#!/usr/bin/env python3
"""Run a config-driven Button Men adventure described by a TOML file."""

import argparse
import fcntl
import json
import logging
import re
import time
import tomllib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter

import bmutils


LOGGER = logging.getLogger("watchadventure")
RECORD_PREFIX = "ADVENTURE_RECORD_V1 "
REJECTION_PREFIX = "ADVENTURE_REJECTION_V1 "
LEADERBOARD_MARKER_VERSION = "ADVENTURE_LEADERBOARD_V4"
BUTTON_TAG_RE = re.compile(
    r'\[button\s*=\s*(?:"([^"\]\n]+)"|\'([^\'\]\n]+)\'|([^\]"\'\n]+))\]',
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
class FightDefinition:
  opponent_button: str
  label: str
  description: str
  emoji: str


@dataclass(frozen=True)
class Templates:
  mission_header: str
  journey_title: str
  champion_line: str
  accepted_from_line: str
  fight_line: str
  creating_conclusion: str
  active_conclusion: str
  survived_conclusion: str
  failed_conclusion: str
  rejection_post: str
  game_description: str
  leaderboard_title: str
  leaderboard_intro: str
  leaderboard_empty: str
  leaderboard_section: str
  leaderboard_entry: str
  leaderboard_ineligible_title: str
  leaderboard_ineligible_entry: str


@dataclass(frozen=True)
class AdventureConfig:
  source_path: Path
  slug: str
  name: str
  thread_id: int
  bot_player: str
  acceptance_command: str
  entry_mode: str
  games_created_after: int | None
  submissions_close_at: int | None
  entry_target_wins: int
  show_ineligible_games: bool
  button_uniqueness: str
  minimum_win_rate: float | None
  maximum_win_rate: float | None
  require_published_win_rate: bool
  require_bot_readable: bool
  supported_button_exceptions: frozenset[str]
  win_rate_allowlist: str | None
  button_filter_url: str
  buttonmen_tools_dir: Path
  credentials_file: Path
  credentials_site: str
  sleep_seconds: int
  state_directory: Path
  leaderboard_enabled: bool
  leaderboard_post_id: int | None
  leaderboard_group_by_opponent: bool
  announcement_post_id: int | None
  fights: tuple[FightDefinition, ...]
  templates: Templates
  import_looking_glass_v2: bool

  @property
  def fight_count(self):
    return len(self.fights)

  @property
  def lock_file(self):
    return self.state_directory / f"{self.slug}.lock"


@dataclass(frozen=True)
class Acceptance:
  post_id: int
  player: str
  button: str
  posted_at: int = 0


@dataclass(frozen=True)
class FightLog:
  number: int
  game_id: int
  opponent: str
  result: str


@dataclass(frozen=True)
class QuestLog:
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
  name: str
  bot_can_read: bool
  win_rate: float | None


@dataclass(frozen=True)
class LeaderboardEntry:
  player: str
  button: str
  opponents_defeated: int
  rounds_lost: int
  points_scored: float
  status: str
  defeated_by: str | None


@dataclass(frozen=True)
class IneligibleGame:
  game_id: int
  player: str
  button: str
  reason: str


def _resolve_path(config_path, value):
  path = Path(value).expanduser()
  return path if path.is_absolute() else (config_path.parent / path).resolve()


def _required(section, key, section_name):
  if key not in section:
    raise ValueError(f"Missing [{section_name}] {key}")
  return section[key]


def _utc_timestamp(value):
  """Parse an ISO date or timestamp, treating a date as midnight UTC."""
  if value is None:
    return None
  text = str(value).strip()
  if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
    text += "T00:00:00+00:00"
  elif text.endswith("Z"):
    text = text[:-1] + "+00:00"
  parsed = datetime.fromisoformat(text)
  if parsed.tzinfo is None:
    parsed = parsed.replace(tzinfo=timezone.utc)
  return int(parsed.timestamp())


def load_config(path):
  """Load and validate one complete adventure definition."""
  source_path = Path(path).expanduser().resolve()
  with source_path.open("rb") as config_file:
    raw = tomllib.load(config_file)

  adventure = raw.get("adventure", {})
  entry = raw.get("entry", {})
  eligibility = raw.get("eligibility", {})
  runtime = raw.get("runtime", {})
  forum = raw.get("forum", {})
  leaderboard = raw.get("leaderboard", {})
  compatibility = raw.get("compatibility", {})
  template_values = raw.get("templates", {})

  if raw.get("version") != 1:
    raise ValueError("Adventure config version must be 1")
  slug = _required(adventure, "slug", "adventure")
  if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
    raise ValueError(
        "[adventure] slug must contain lowercase letters, numbers, and hyphens")
  uniqueness = _required(
      adventure, "button_uniqueness", "adventure").lower()
  if uniqueness not in {"global", "player", "none"}:
    raise ValueError(
        "[adventure] button_uniqueness must be global, player, or none")
  entry_mode = entry.get("mode", "forum_post").lower()
  if entry_mode not in {"forum_post", "completed_game"}:
    raise ValueError("[entry] mode must be forum_post or completed_game")
  games_created_after = _utc_timestamp(entry.get("games_created_after"))
  submissions_close_at = _utc_timestamp(entry.get("submissions_close_at"))
  if entry_mode == "completed_game" and games_created_after is None:
    raise ValueError(
        "[entry] completed_game mode requires games_created_after")
  if (submissions_close_at is not None and games_created_after is not None and
      submissions_close_at <= games_created_after):
    raise ValueError(
        "[entry] submissions_close_at must be after games_created_after")
  entry_target_wins = int(entry.get("target_wins", 3))
  if entry_target_wins <= 0:
    raise ValueError("[entry] target_wins must be positive")

  fights = tuple(
      FightDefinition(
          opponent_button=_required(item, "opponent_button", "fights"),
          label=item.get("label", item.get("opponent_button", "")),
          description=item.get("description", ""),
          emoji=item.get("emoji", "❌"),
      )
      for item in raw.get("fights", [])
  )
  if not fights:
    raise ValueError("At least one [[fights]] entry is required")

  defaults = {
      "mission_header": "[MISSION: {player} - {button}]",
      "journey_title": "{player}'s {adventure_name} journey",
      "champion_line": "Champion: {button}",
      "accepted_from_line": "Accepted from: {source_post_link}",
      "fight_line": (
          "Fight {fight_number}/{fight_count} — {opponent}: "
          "[game={game_id}] — {fight_result_display}"),
      "creating_conclusion": (
          "Assignment accepted. Preparing fight 1/{fight_count}."),
      "active_conclusion": "The adventure continues.",
      "survived_conclusion": "The champion completed {adventure_name}.",
      "failed_conclusion": (
          "The champion fell to {defeated_by}. This adventure has ended."),
      "rejection_post": (
          "{player}, this assignment cannot be accepted.\n\n{reason}.\n\n"
          "Acceptance: {source_post_link}\n\n"
          "Use an exact published button name:\n"
          "[code]{acceptance_command} [button=Aylee]\n"
          "{acceptance_command} Aylee[/code]\n\n{button_filter_url}"),
      "game_description": (
          "{adventure_name} FIGHT {fight_number}/{fight_count} | "
          "MISSION {source_post_id}"),
      "leaderboard_title": "[b]{adventure_name} — COMMUNITY LEADERBOARD[/b]",
      "leaderboard_intro": (
          "Columns: opponents defeated / rounds lost / points scored. Ranked "
          "by opponents defeated, then fewest rounds lost, then most points "
          "scored."),
      "leaderboard_empty": "No completed adventures yet.",
      "leaderboard_section": "[b]{section}[/b]",
      "leaderboard_entry": (
          "{rank}. {player} — {button} — "
          "{opponents_defeated}F/{rounds_lost}L/{points_scored:g}pts — "
          "{outcome}"),
      "leaderboard_ineligible_title": "[b]INELIGIBLE ENTRY GAMES[/b]",
      "leaderboard_ineligible_entry": (
          "[game={game_id}] {player} — {button} — {reason}"),
  }
  templates = Templates(**{
      key: template_values.get(key, default)
      for key, default in defaults.items()
  })
  if (uniqueness == "none" and not any(
      field in templates.game_description
      for field in ("{source_post_id}", "{entry_game_id}"))):
    raise ValueError(
        "button_uniqueness=none requires a mission ID placeholder in the "
        "game_description template so simultaneous missions stay distinct")

  thread_id = int(_required(adventure, "thread_id", "adventure"))
  if thread_id <= 0:
    raise ValueError("[adventure] thread_id must be positive")
  sleep_seconds = int(runtime.get("sleep_seconds", 120))
  if sleep_seconds <= 0:
    raise ValueError("[runtime] sleep_seconds must be positive")

  minimum = eligibility.get("minimum_win_rate")
  maximum = eligibility.get("maximum_win_rate")
  minimum = float(minimum) if minimum is not None else None
  maximum = float(maximum) if maximum is not None else None
  if minimum is not None and maximum is not None and minimum >= maximum:
    raise ValueError("minimum_win_rate must be less than maximum_win_rate")

  leaderboard_enabled = bool(leaderboard.get("enabled", False))
  leaderboard_post_id = leaderboard.get("post_id")
  if leaderboard_enabled and not leaderboard_post_id:
    raise ValueError("An enabled leaderboard requires [leaderboard] post_id")
  if entry_mode == "completed_game" and not leaderboard_enabled:
    raise ValueError("completed_game entry mode requires an enabled leaderboard")

  config = AdventureConfig(
      source_path=source_path,
      slug=slug,
      name=_required(adventure, "name", "adventure"),
      thread_id=thread_id,
      bot_player=adventure.get("bot_player", "BMAIBagels"),
      acceptance_command=adventure.get("acceptance_command", "I accept"),
      entry_mode=entry_mode,
      games_created_after=games_created_after,
      submissions_close_at=submissions_close_at,
      entry_target_wins=entry_target_wins,
      show_ineligible_games=bool(entry.get("show_ineligible_games", False)),
      button_uniqueness=uniqueness,
      minimum_win_rate=minimum,
      maximum_win_rate=maximum,
      require_published_win_rate=bool(
          eligibility.get("require_published_win_rate", True)),
      require_bot_readable=bool(
          eligibility.get("require_bot_readable", True)),
      supported_button_exceptions=frozenset(
          eligibility.get("supported_button_exceptions", [])),
      win_rate_allowlist=eligibility.get("win_rate_allowlist"),
      button_filter_url=eligibility.get("button_filter_url", ""),
      buttonmen_tools_dir=_resolve_path(
          source_path, runtime.get("buttonmen_tools_dir", "../..")),
      credentials_file=_resolve_path(
          source_path, runtime.get("credentials_file", "../.bmrc")),
      credentials_site=runtime.get("credentials_site", "bmaibagels"),
      sleep_seconds=sleep_seconds,
      state_directory=_resolve_path(
          source_path, runtime.get("state_directory", "../.adventure-state")),
      leaderboard_enabled=leaderboard_enabled,
      leaderboard_post_id=(
          int(leaderboard_post_id) if leaderboard_post_id else None),
      leaderboard_group_by_opponent=bool(
          leaderboard.get("group_by_opponent", False)),
      announcement_post_id=(
          int(forum.get("announcement_post_id"))
          if forum.get("announcement_post_id") else None),
      fights=fights,
      templates=templates,
      import_looking_glass_v2=bool(
          compatibility.get("import_looking_glass_v2", False)),
  )
  validate_templates(config)
  return config


def template_context(config, **values):
  context = {
      "adventure_name": config.name,
      "adventure_slug": config.slug,
      "thread_id": config.thread_id,
      "fight_count": config.fight_count,
      "acceptance_command": config.acceptance_command,
      "button_filter_url": config.button_filter_url,
  }
  context.update(values)
  return context


def render_template(config, text, **values):
  return text.format(**template_context(config, **values))


def validate_templates(config):
  """Fail at startup instead of halfway through a forum write."""
  sample = {
      "player": "Player", "button": "Button", "source_post_id": 1,
      "entry_game_id": 1,
      "source_post_link": "[forum=1,1]source post[/forum]", "reason": "Reason",
      "fight_number": 1, "opponent": "Opponent", "game_id": 1,
      "fight_result": "active", "fight_result_display": "IN PROGRESS",
      "defeated_by": "Opponent", "rank": 1, "opponents_defeated": 0,
      "rounds_lost": 0, "points_scored": 0.0, "outcome": "ACTIVE",
      "section": "SECTION",
  }
  for field in Templates.__dataclass_fields__:
    try:
      render_template(config, getattr(config.templates, field), **sample)
    except KeyError as error:
      raise ValueError(
          f"Unknown placeholder {error} in [templates] {field}") from error


class ButtonFilterPublication:
  """Read button identity, support, and win rate from buttonmen-tools."""

  CAPABILITY_NAMES = (
      "bmaibagels_supported_button_name",
      "bmaibagels_unsupported_button_name",
      "bmaibagels_unsupported_button_set",
      "bmaibagels_supported_die_features",
  )

  def __init__(self, tools_directory):
    self.public_directory = Path(tools_directory) / "public"
    self.reload()

  def reload(self):
    self.button_data = json.loads(
        (self.public_directory / "buttondata.json").read_text(encoding="utf-8"))
    self.button_stats = json.loads(
        (self.public_directory / "buttonstats.json").read_text(encoding="utf-8"))
    self.filter_html = (self.public_directory / "ButtonFilter.html").read_text(
        encoding="utf-8")
    self.capabilities = {
        name: set(self._javascript_array(name)) for name in self.CAPABILITY_NAMES
    }
    self.allowlists = {}
    self.buttons_by_case = {
        button["buttonName"].casefold(): button
        for button in self.button_data["data"]
    }
    self.stats_by_case = {
        name.casefold(): stats
        for name, stats in self.button_stats["data"].items()
    }

  def _javascript_array(self, name):
    match = re.search(rf"\b{re.escape(name)}\s*=\s*(\[[\s\S]*?\])",
                      self.filter_html)
    if not match:
      raise ValueError(f"Could not find {name} in ButtonFilter.html")
    return json.loads(match.group(1))

  def eligibility(self, button_name):
    button = self.buttons_by_case.get(button_name.casefold())
    if button is None:
      return None
    name = button["buttonName"]
    features = set(button["dieSkills"] + button["dieTypes"])
    can_read = (
        name in self.capabilities["bmaibagels_supported_button_name"] or (
            name not in self.capabilities["bmaibagels_unsupported_button_name"]
            and button["buttonSet"] not in
            self.capabilities["bmaibagels_unsupported_button_set"]
            and features <=
            self.capabilities["bmaibagels_supported_die_features"]
        )
    )
    stats = self.stats_by_case.get(name.casefold())
    rate = float(stats["rate"]) if stats is not None else None
    return ButtonEligibility(name, can_read, rate)

  def button_in_allowlist(self, button_name, allowlist_name):
    if not allowlist_name:
      return False
    return button_name.casefold() in self.load_allowlist(allowlist_name)

  def load_allowlist(self, allowlist_name):
    if allowlist_name not in self.allowlists:
      self.allowlists[allowlist_name] = {
          name.casefold() for name in self._javascript_array(allowlist_name)
      }
    return self.allowlists[allowlist_name]


def parse_acceptance(post, config):
  if (post.get("deleted") or
      post.get("posterName", "").casefold() == config.bot_player.casefold()):
    return None
  body = post.get("body", "")
  command_re = re.compile(
      rf"^\s*{re.escape(config.acceptance_command)}\b\s*(.*?)\s*$",
      re.IGNORECASE,
  )
  command = command_re.search(body)
  if not command:
    return None
  tagged = BUTTON_TAG_RE.search(body)
  if tagged:
    button = next(value for value in tagged.groups() if value is not None)
  else:
    button = command.group(1).strip()
    if (len(button) >= 2 and button[0] == button[-1]
        and button[0] in ('"', "'")):
      button = button[1:-1].strip()
    if button.startswith("["):
      button = ""
  return Acceptance(
      int(post["postId"]), post["posterName"].strip(), button.strip(),
      int(post.get("creationTime") or 0))


def _record_json(body, prefix):
  pattern = re.compile(
      rf"\[code\]{re.escape(prefix)}([^\n]*?)\[/code\]", re.IGNORECASE)
  match = pattern.search(body)
  return json.loads(match.group(1)) if match else None


def _quest_from_record(post, record):
  fights = tuple(FightLog(
      int(fight["number"]), int(fight["game_id"]), fight["opponent"],
      fight["result"])
      for fight in record.get("fights", []))
  return QuestLog(
      int(post["postId"]), int(record["source_post_id"]), record["player"],
      record["button"], record["status"], fights)


def _legacy_looking_glass_log(post, config):
  body = post.get("body", "")
  mission = re.match(
      r"^\[MISSION:\s*(.+?)\s+-\s+(.+?)\]\s*$",
      body.split("\n", 1)[0], re.IGNORECASE)
  header = re.search(
      r"OPERATION LOOKING GLASS\s*\|\s*source-post=(\d+)\s*\|\s*"
      r"player=([^|\n]+?)\s*\|\s*button=([^|\n]+?)\s*\|\s*"
      r"status=(creating|active|failed|survived)", body, re.IGNORECASE)
  if not header:
    oldest = re.search(
        r"OPERATION LOOKING GLASS\s*\|\s*source-post=(\d+)\s*\|\s*"
        r"player=([^|\n]+?)\s*\|\s*button=([^|\n]+?)\s*\|\s*"
        r"game=(\d+)\s*\|\s*status=(active|complete)",
        body, re.IGNORECASE)
    if not oldest:
      return None
    status = "active" if oldest.group(5).lower() == "active" else "failed"
    return QuestLog(
        int(post["postId"]), int(oldest.group(1)),
        mission.group(1).strip() if mission else oldest.group(2).strip(),
        mission.group(2).strip() if mission else oldest.group(3).strip(),
        status,
        (FightLog(
            1, int(oldest.group(4)), config.fights[0].label,
            "active" if status == "active" else "lost"),),
    )
  fight_re = re.compile(
      r"LOOKING GLASS FIGHT\s*\|\s*number=(\d+)\s*\|\s*game=(\d+)\s*"
      r"\|\s*opponent=([^|\n]+?)\s*\|\s*result=(active|won|lost)",
      re.IGNORECASE)
  fights = tuple(FightLog(
      int(match.group(1)), int(match.group(2)), match.group(3).strip(),
      match.group(4).lower()) for match in fight_re.finditer(body))
  status = header.group(4).lower()
  if status != "creating" and not fights:
    return None
  return QuestLog(
      int(post["postId"]), int(header.group(1)),
      mission.group(1).strip() if mission else header.group(2).strip(),
      mission.group(2).strip() if mission else header.group(3).strip(),
      status, fights)


def parse_quest_logs(posts, config):
  logs = []
  for post in posts:
    if (post.get("deleted") or
        post.get("posterName", "").casefold() != config.bot_player.casefold()):
      continue
    record = _record_json(post.get("body", ""), RECORD_PREFIX)
    if record and record.get("adventure") == config.slug:
      logs.append(_quest_from_record(post, record))
    elif config.import_looking_glass_v2:
      legacy = _legacy_looking_glass_log(post, config)
      if legacy:
        logs.append(legacy)
  authoritative = {}
  for log in sorted(logs, key=lambda item: item.forum_post_id):
    authoritative.setdefault(log.source_post_id, log)
  return list(authoritative.values())


def parse_rejected_source_posts(posts, config):
  rejected = set()
  legacy_re = re.compile(
      r"OPERATION LOOKING GLASS\s*\|\s*source-post=(\d+)\s*\|\s*"
      r"decision=rejected", re.IGNORECASE)
  for post in posts:
    if (post.get("deleted") or
        post.get("posterName", "").casefold() != config.bot_player.casefold()):
      continue
    body = post.get("body", "")
    record = _record_json(body, REJECTION_PREFIX)
    if record and record.get("adventure") == config.slug:
      rejected.add(int(record["source_post_id"]))
    if config.import_looking_glass_v2:
      rejected.update(int(value) for value in legacy_re.findall(body))
  return rejected


def posted_after_intake_closed(acceptance, config):
  """Acceptances posted once intake closed get no reply; missions already
  begun play on."""
  close = config.submissions_close_at
  return close is not None and acceptance.posted_at >= close


def duplicate_rejection_reason(acceptance, logs, config):
  same_button = [
      log for log in logs
      if log.button.casefold() == acceptance.button.casefold()
  ]
  duplicate = None
  if config.button_uniqueness == "global" and same_button:
    duplicate = same_button[0]
  elif config.button_uniqueness == "player":
    duplicate = next((
        log for log in same_button
        if log.player.casefold() == acceptance.player.casefold()), None)
  if duplicate is None:
    return None
  if config.button_uniqueness == "global":
    return (
        f"{duplicate.button} has already been attempted by {duplicate.player}; "
        "each button may enter this adventure only once")
  return (
      f"{acceptance.player} has already attempted this adventure with "
      f"{duplicate.button}; each player may use a given button only once")


def eligibility_rejection_reason(eligibility, config, button_filter=None):
  readable = (
      eligibility.bot_can_read or
      eligibility.name in config.supported_button_exceptions)
  if config.require_bot_readable and not readable:
    return f"{config.bot_player} cannot read {eligibility.name}"
  grandfathered = (
      button_filter is not None and config.win_rate_allowlist and
      button_filter.button_in_allowlist(
          eligibility.name, config.win_rate_allowlist))
  if grandfathered:
    return None
  if eligibility.win_rate is None:
    if config.require_published_win_rate:
      return f"{eligibility.name} has no published win rate"
    return None
  if (config.minimum_win_rate is not None and
      eligibility.win_rate < config.minimum_win_rate):
    return (
        f"{eligibility.name} has a published win rate of "
        f"{eligibility.win_rate:g}%; this adventure requires at least "
        f"{config.minimum_win_rate:g}%")
  if (config.maximum_win_rate is not None and
      eligibility.win_rate >= config.maximum_win_rate):
    return (
        f"{eligibility.name} has a published win rate of "
        f"{eligibility.win_rate:g}%; this adventure requires less than "
        f"{config.maximum_win_rate:g}%")
  return None


def source_post_link(config, post_id):
  return f"[forum={config.thread_id},{post_id}]source post[/forum]"


def render_rejection(acceptance, reason, config):
  human = render_template(
      config, config.templates.rejection_post,
      player=acceptance.player, button=acceptance.button, reason=reason,
      source_post_id=acceptance.post_id,
      source_post_link=source_post_link(config, acceptance.post_id),
  ).strip()
  record = json.dumps({
      "adventure": config.slug,
      "source_post_id": acceptance.post_id,
  }, separators=(",", ":"), sort_keys=True)
  return f"{human}\n\n[code]{REJECTION_PREFIX}{record}[/code]"


def quest_record(quest, config):
  return {
      "adventure": config.slug,
      "source_post_id": quest.source_post_id,
      "player": quest.player,
      "button": quest.button,
      "status": quest.status,
      "fights": [
          {"number": fight.number, "game_id": fight.game_id,
           "opponent": fight.opponent, "result": fight.result}
          for fight in quest.fights
      ],
  }


def render_quest(quest, config):
  defeated_by = next((
      fight.opponent for fight in quest.fights if fight.result == "lost"), "")
  common = {
      "player": quest.player,
      "button": quest.button,
      "source_post_id": quest.source_post_id,
      "source_post_link": source_post_link(config, quest.source_post_id),
      "defeated_by": defeated_by,
  }
  lines = [
      render_template(config, config.templates.mission_header, **common),
      render_template(config, config.templates.journey_title, **common),
      "",
      render_template(config, config.templates.champion_line, **common),
      render_template(config, config.templates.accepted_from_line, **common),
      "",
  ]
  for fight in quest.fights:
    display = "IN PROGRESS" if fight.result == "active" else fight.result.upper()
    lines.append(render_template(
        config, config.templates.fight_line, **common,
        fight_number=fight.number, opponent=fight.opponent,
        game_id=fight.game_id, fight_result=fight.result,
        fight_result_display=display,
    ))
  conclusion_template = {
      "creating": config.templates.creating_conclusion,
      "active": config.templates.active_conclusion,
      "survived": config.templates.survived_conclusion,
      "failed": config.templates.failed_conclusion,
  }[quest.status]
  lines.extend(("", render_template(
      config, conclusion_template, **common), ""))
  record = json.dumps(
      quest_record(quest, config), separators=(",", ":"), sort_keys=True)
  lines.append(f"[code]{RECORD_PREFIX}{record}[/code]")
  return "\n".join(lines)


def mission_game_identity(config, fight_number, source_post_id):
  fight = config.fights[fight_number - 1]
  return render_template(
      config, config.templates.game_description,
      fight_number=fight_number, opponent=fight.label,
      source_post_id=source_post_id, entry_game_id=source_post_id)


def mission_game_description(config, fight_number, source_post_id):
  identity = mission_game_identity(config, fight_number, source_post_id)
  flavor = config.fights[fight_number - 1].description.strip()
  return f"{identity} | {flavor}" if flavor else identity


def game_description_matches(config, fight_number, source_post_id, actual):
  identity = mission_game_identity(config, fight_number, source_post_id)
  return actual == identity or actual.startswith(f"{identity} | ")


def game_description_identifies_mission(config):
  return any(
      field in config.templates.game_description
      for field in ("{source_post_id}", "{entry_game_id}"))


def completed_game_adventure_can_retire(config, quests, available_games,
                                        now=None):
  """Return whether closed intake and all visible mission work are finished."""
  if (config.entry_mode != "completed_game" or
      config.submissions_close_at is None):
    return False
  current_time = int(time.time()) if now is None else int(now)
  if current_time < config.submissions_close_at:
    return False
  if any(quest.status in {"creating", "active"} for quest in quests):
    return False
  gate_button = config.fights[0].opponent_button.casefold()
  return not any(
      game["myButtonName"].casefold() == gate_button and
      int(game["nTargetWins"]) == config.entry_target_wins
      for game in available_games
  )


def validate_opponents(config, button_filter):
  """Verify locally that every bot-controlled opponent is playable."""
  if config.win_rate_allowlist:
    button_filter.load_allowlist(config.win_rate_allowlist)
  for number, fight in enumerate(config.fights, 1):
    eligibility = button_filter.eligibility(fight.opponent_button)
    if eligibility is None:
      raise ValueError(
          f"Fight {number} opponent {fight.opponent_button!r} is not an "
          "exact published production button")
    readable = (
        eligibility.bot_can_read or
        eligibility.name in config.supported_button_exceptions)
    if config.require_bot_readable and not readable:
      raise ValueError(
          f"Fight {number} opponent {eligibility.name!r} cannot be played "
          f"by {config.bot_player}")


def player_data(game, player_name):
  return next(player for player in game["playerDataArray"]
              if player["playerName"].casefold() == player_name.casefold())


def points_scored_in_game(game, player_name):
  total = 0.0
  for item in game.get("gameActionLog", []):
    message = item.get("message", "")
    win = ROUND_WIN_RE.match(message)
    if win:
      winner, winning_score, losing_score = win.groups()
      total += float(
          winning_score if winner.casefold() == player_name.casefold()
          else losing_score)
    else:
      draw = ROUND_DRAW_RE.match(message)
      if draw:
        total += float(draw.group(1))
  return total


def build_leaderboard(logs, load_game_data, include_active=False):
  entries = []
  for quest in logs:
    if quest.status not in {"failed", "survived"} and not (
        include_active and quest.status == "active"):
      continue
    rounds_lost = 0
    points = 0.0
    for fight in quest.fights:
      if fight.result == "active":
        continue
      game = load_game_data(fight.game_id)
      rounds_lost += int(
          player_data(game, quest.player)["gameScoreArray"]["L"])
      points += points_scored_in_game(game, quest.player)
    entries.append(LeaderboardEntry(
        quest.player, quest.button,
        sum(fight.result == "won" for fight in quest.fights),
        rounds_lost, points, quest.status,
        next((fight.opponent for fight in quest.fights
              if fight.result == "lost"), None),
    ))
  return sorted(entries, key=lambda entry: (
      -entry.opponents_defeated, entry.rounds_lost, -entry.points_scored,
      entry.player.casefold(), entry.button.casefold()))


def _without_entry_bold(line):
  stripped = line.strip()
  if stripped.startswith("[b]") and stripped.endswith("[/b]"):
    return stripped[3:-4]
  return line


def _template_row_regex(template, captured_fields):
  parts = []
  captured = set()
  for literal, field, _format_spec, _conversion in Formatter().parse(template):
    parts.append(re.escape(literal))
    if field is None:
      continue
    if field in captured_fields and field not in captured:
      parts.append(fr"(?P<{field}>.+?)")
      captured.add(field)
    elif field in captured:
      parts.append(fr"(?P={field})")
    else:
      parts.append(r".+?")
  return re.compile("^" + "".join(parts) + "$")


def _previous_leaderboard_lines(body, config):
  """Index rendered rows while allowing repeated player/button identities."""
  rows = {}
  occurrences = {}
  entry_re = _template_row_regex(
      config.templates.leaderboard_entry, {"player", "button"})
  ineligible_re = _template_row_regex(
      config.templates.leaderboard_ineligible_entry, {"game_id"})
  for raw_line in body.splitlines():
    line = _without_entry_bold(raw_line)
    entry = entry_re.fullmatch(line)
    if entry:
      pair = (
          entry.group("player").casefold(), entry.group("button").casefold())
      occurrence = occurrences.get(pair, 0)
      occurrences[pair] = occurrence + 1
      rows[("entry", *pair, occurrence)] = line
      continue
    ineligible = ineligible_re.fullmatch(line)
    if ineligible:
      rows[("ineligible", int(ineligible.group("game_id")))] = line
  return rows


def _body_uses_current_leaderboard_format(body, config):
  """Return whether mission rows use the configured leaderboard template.

  Ineligible-game rows have their own template and cannot prove that mission
  rows use the current format. Treating either row type as sufficient caused
  every Oz mission to look new when only the mission-row template changed.
  """
  entry_re = _template_row_regex(
      config.templates.leaderboard_entry, {"player", "button"})
  for raw_line in body.splitlines():
    line = _without_entry_bold(raw_line)
    if entry_re.fullmatch(line):
      return True
  return render_template(
      config, config.templates.leaderboard_empty) in body


def _row_comparison(line, key, config):
  """Ignore rank movement when deciding whether an existing row changed."""
  if key[0] == "entry":
    match = _template_row_regex(
        config.templates.leaderboard_entry, {"rank"}).fullmatch(line)
    if match:
      start, end = match.span("rank")
      return line[:start] + "{rank}" + line[end:]
  return line


def leaderboard_content_equal(first, second):
  """Treat row highlighting as presentation state, not a board change."""
  def normalized(body):
    return "\n".join(_without_entry_bold(line) for line in body.splitlines())
  return normalized(first) == normalized(second)


def render_leaderboard(
    entries, config, ineligible_games=(), previous_body=None):
  lines = [
      render_template(config, config.templates.leaderboard_title), "",
      render_template(config, config.templates.leaderboard_intro), "",
  ]
  marker = f"[code]{LEADERBOARD_MARKER_VERSION} {config.slug}[/code]"
  highlight_changes = (
      previous_body is not None and marker in previous_body and
      _body_uses_current_leaderboard_format(previous_body, config))
  previous_rows = (
      _previous_leaderboard_lines(previous_body, config)
      if highlight_changes else {})
  occurrences = {}
  if not entries:
    lines.append(render_template(config, config.templates.leaderboard_empty))
  previous_score = None
  previous_defeated = None
  previous_section = None
  rank = 0
  for position, entry in enumerate(entries, 1):
    if config.leaderboard_group_by_opponent:
      section = (
          "SURVIVED" if entry.status == "survived" else
          config.fights[min(
              entry.opponents_defeated, config.fight_count - 1)].label.upper())
      if section != previous_section:
        if previous_section is not None:
          lines.append("")
        lines.append(render_template(
            config, config.templates.leaderboard_section, section=section))
        previous_section = section
    elif (previous_defeated is not None and
          entry.opponents_defeated != previous_defeated):
      lines.append("")
    previous_defeated = entry.opponents_defeated
    score = (entry.opponents_defeated, entry.rounds_lost, entry.points_scored)
    if score != previous_score:
      rank = position
      previous_score = score
    if config.leaderboard_group_by_opponent:
      outcome = {
          "failed": config.fights[min(
              entry.opponents_defeated, config.fight_count - 1)].emoji,
          "survived": "🏆",
          "active": "⏳",
      }[entry.status]
    else:
      outcome = {
          "failed": f"DEFEATED by {entry.defeated_by}",
          "survived": "SURVIVED",
          "active": "MISSION IN PROGRESS",
      }[entry.status]
    line = render_template(
        config, config.templates.leaderboard_entry,
        rank=rank, player=entry.player, button=entry.button,
        opponents_defeated=entry.opponents_defeated,
        rounds_lost=entry.rounds_lost, points_scored=entry.points_scored,
        outcome=outcome,
    )
    pair = (entry.player.casefold(), entry.button.casefold())
    occurrence = occurrences.get(pair, 0)
    occurrences[pair] = occurrence + 1
    key = ("entry", *pair, occurrence)
    prior = previous_rows.get(key)
    if highlight_changes and (
        prior is None or
        _row_comparison(prior, key, config) !=
        _row_comparison(line, key, config)):
      line = f"[b]{line}[/b]"
    lines.append(line)
  if ineligible_games:
    lines.extend(("", render_template(
        config, config.templates.leaderboard_ineligible_title)))
    for game in ineligible_games:
      line = render_template(
          config, config.templates.leaderboard_ineligible_entry,
          game_id=game.game_id, player=game.player, button=game.button,
          reason=game.reason)
      key = ("ineligible", game.game_id)
      prior = previous_rows.get(key)
      if highlight_changes and prior != line:
        line = f"[b]{line}[/b]"
      lines.append(line)
  lines.extend(("", marker))
  return "\n".join(lines)


def history_players(game):
  """Return summary participants as (player, button, rounds won) tuples."""
  return (
      (game["playerNameA"], game["buttonNameA"], int(game["roundsWonA"])),
      (game["playerNameB"], game["buttonNameB"], int(game["roundsWonB"])),
  )


def history_match(game, bot_player, bot_button):
  """Return the human participant when the other side is the requested bot."""
  first, second = history_players(game)
  if (first[0].casefold() == bot_player.casefold() and
      first[1].casefold() == bot_button.casefold()):
    return second, first
  if (second[0].casefold() == bot_player.casefold() and
      second[1].casefold() == bot_button.casefold()):
    return first, second
  return None


def history_player_won(game, player_name):
  player, opponent = history_players(game)
  if player[0].casefold() != player_name.casefold():
    player, opponent = opponent, player
  return player[2] > opponent[2]


class AdventureMonitor:

  def __init__(self, client, button_filter, config):
    self.client = client
    self.button_filter = button_filter
    self.config = config

  def create_fight(self, quest, number, available_games):
    definition = self.config.fights[number - 1]
    expected_description = mission_game_description(
        self.config, number, quest.source_post_id)
    matches = [game for game in available_games
               if game["opponentName"].casefold() == quest.player.casefold()
               and game["myButtonName"].casefold() ==
               definition.opponent_button.casefold()
               and game["opponentButtonName"].casefold() ==
               quest.button.casefold()]
    if game_description_identifies_mission(self.config):
      matches = [
          game for game in matches
          if game_description_matches(
              self.config, number, quest.source_post_id,
              self.client.wrap_load_game_data(
                  int(game["gameId"])).get("description", ""))
      ]
    if matches:
      game_id = min(int(game["gameId"]) for game in matches)
      LOGGER.warning(
          "Adopting existing game: adventure=%s player=%s button=%s "
          "fight=%s game=%s", self.config.slug, quest.player, quest.button,
          number, game_id)
    else:
      result = self.client.wrap_create_game(
          definition.opponent_button, quest.button, self.config.bot_player,
          quest.player, expected_description)
      game_id = int(result["gameId"])
    return FightLog(number, game_id, definition.label, "active")

  @staticmethod
  def player_won(game, player_name):
    player = player_data(game, player_name)
    opponent = next(item for item in game["playerDataArray"]
                    if item is not player)
    return int(player["gameScoreArray"]["W"]) > int(
        opponent["gameScoreArray"]["W"])

  def edit_quest(self, quest):
    self.client.wrap_edit_forum_post(
        quest.forum_post_id, render_quest(quest, self.config))

  def reconcile_quest(self, quest, available_games):
    if quest.status == "creating":
      fight = self.create_fight(quest, 1, available_games)
      updated = replace(quest, status="active", fights=(fight,))
      self.edit_quest(updated)
      LOGGER.info(
          "Adventure started: adventure=%s player=%s button=%s game=%s",
          self.config.slug, quest.player, quest.button, fight.game_id)
      return updated

    current = quest.current_fight
    if current.game_id in {int(game["gameId"]) for game in available_games}:
      return quest
    game = self.client.wrap_load_game_data(current.game_id)
    if game["gameState"] not in {"END_GAME", "CANCELLED"}:
      return quest
    won = game["gameState"] == "END_GAME" and self.player_won(
        game, quest.player)
    resolved = replace(current, result="won" if won else "lost")
    fights = quest.fights[:-1] + (resolved,)
    if not won:
      updated = replace(quest, status="failed", fights=fights)
      self.edit_quest(updated)
      LOGGER.info(
          "Adventure failed: adventure=%s player=%s button=%s opponent=%s",
          self.config.slug, quest.player, quest.button, current.opponent)
      return updated
    if current.number == self.config.fight_count:
      updated = replace(quest, status="survived", fights=fights)
      self.edit_quest(updated)
      LOGGER.info(
          "Adventure survived: adventure=%s player=%s button=%s",
          self.config.slug, quest.player, quest.button)
      return updated
    next_fight = self.create_fight(
        quest, current.number + 1, available_games)
    updated = replace(quest, fights=fights + (next_fight,))
    self.edit_quest(updated)
    LOGGER.info(
        "Adventure advanced: adventure=%s player=%s button=%s fight=%s "
        "game=%s", self.config.slug, quest.player, quest.button,
        next_fight.number, next_fight.game_id)
    return updated

  def reject(self, acceptance, reason):
    self.client.wrap_create_forum_post(
        self.config.thread_id,
        render_rejection(acceptance, reason, self.config))
    LOGGER.info(
        "Assignment rejected: adventure=%s post=%s player=%s button=%s "
        "reason=%s", self.config.slug, acceptance.post_id,
        acceptance.player, acceptance.button, reason)

  def check_once(self):
    self.config.state_directory.mkdir(parents=True, exist_ok=True)
    with self.config.lock_file.open("a+", encoding="utf-8") as lock_file:
      fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
      return self._check_once_locked()

  def _check_once_locked(self):
    self.button_filter.reload()
    posts = self.client.wrap_load_forum_thread(
        self.config.thread_id)["posts"]
    available = self.client.wrap_load_active_games()
    available.extend(self.client.wrap_load_new_games())
    logs = [
        self.reconcile_quest(log, available)
        if log.status in {"creating", "active"} else log
        for log in parse_quest_logs(posts, self.config)
    ]
    rejected = parse_rejected_source_posts(posts, self.config)

    for post in posts:
      acceptance = parse_acceptance(post, self.config)
      if not acceptance:
        continue
      if (acceptance.post_id in rejected or
          any(log.source_post_id == acceptance.post_id for log in logs)):
        continue
      if posted_after_intake_closed(acceptance, self.config):
        continue
      reason = duplicate_rejection_reason(acceptance, logs, self.config)
      if reason:
        self.reject(acceptance, reason)
        rejected.add(acceptance.post_id)
        continue
      eligibility = self.button_filter.eligibility(acceptance.button)
      if eligibility is None:
        reason = (
            f"{acceptance.button!r} is not an exact published production "
            "button name" if acceptance.button else
            "I could not find a button name in that acceptance")
        self.reject(acceptance, reason)
        rejected.add(acceptance.post_id)
        continue
      acceptance = replace(acceptance, button=eligibility.name)
      reason = eligibility_rejection_reason(
          eligibility, self.config, self.button_filter)
      if reason:
        self.reject(acceptance, reason)
        rejected.add(acceptance.post_id)
        continue

      fresh_posts = self.client.wrap_load_forum_thread(
          self.config.thread_id)["posts"]
      fresh_logs = parse_quest_logs(fresh_posts, self.config)
      fresh_rejected = parse_rejected_source_posts(fresh_posts, self.config)
      if (acceptance.post_id in fresh_rejected or any(
          log.source_post_id == acceptance.post_id for log in fresh_logs)):
        continue
      reason = duplicate_rejection_reason(
          acceptance, fresh_logs, self.config)
      if reason:
        self.reject(acceptance, reason)
        continue

      claim = QuestLog(
          0, acceptance.post_id, acceptance.player, acceptance.button,
          "creating", ())
      response = self.client.wrap_create_forum_post(
          self.config.thread_id, render_quest(claim, self.config))
      claim = next(
          log for log in parse_quest_logs(response["posts"], self.config)
          if log.source_post_id == acceptance.post_id)
      logs.append(claim)
      available = self.client.wrap_load_active_games()
      available.extend(self.client.wrap_load_new_games())
      logs[-1] = self.reconcile_quest(claim, available)

    self.update_leaderboard(logs, posts)

  def update_leaderboard(self, logs, posts):
    if not self.config.leaderboard_enabled:
      return
    post = next((item for item in posts if int(item["postId"]) ==
                 self.config.leaderboard_post_id), None)
    if post is None:
      raise ValueError(
          f"Leaderboard post {self.config.leaderboard_post_id} is not in "
          f"thread {self.config.thread_id}")
    entries = build_leaderboard(logs, self.client.wrap_load_game_data)
    previous_body = post.get("body", "")
    body = render_leaderboard(
        entries, self.config, previous_body=previous_body)
    if not leaderboard_content_equal(previous_body, body):
      self.client.wrap_edit_forum_post(self.config.leaderboard_post_id, body)
      LOGGER.info(
          "Leaderboard rewritten: adventure=%s entries=%s",
          self.config.slug, len(entries))

  def run_forever(self):
    while True:
      try:
        logged_in = self.client.verify_login()
        break
      except bmutils.NetworkError as error:
        LOGGER.warning(
            "Network error verifying login; retrying in %s seconds: %s",
            self.config.sleep_seconds, error)
        time.sleep(self.config.sleep_seconds)
    if not logged_in:
      raise RuntimeError("Could not log in with the configured account")
    LOGGER.info(
        "Watching adventure=%s thread=%s every %s seconds",
        self.config.slug, self.config.thread_id, self.config.sleep_seconds)
    last_error = None
    while True:
      try:
        should_exit = self.check_once()
        if last_error:
          LOGGER.info("Monitor recovered after error: %s", last_error)
        last_error = None
        if should_exit:
          LOGGER.info(
              "Adventure watcher exiting: adventure=%s submissions are "
              "closed and no quests remain in progress", self.config.slug)
          return
      except Exception as error:
        message = str(error)
        if message != last_error:
          LOGGER.exception("Adventure monitor check failed")
        else:
          LOGGER.debug("Adventure monitor check failed again: %s", message)
        last_error = message
      time.sleep(self.config.sleep_seconds)


class GameHistoryAdventureMonitor(AdventureMonitor):
  """Use completed games against the first opponent as adventure entries."""

  # searchGameHistory commonly caps pages at 100 even when callers ask for more.
  HISTORY_PAGE_SIZE = 100

  def load_history(self):
    games_by_id = {}
    for player_field in ("playerNameA", "playerNameB"):
      page = 1
      while True:
        query = {
            "sortColumn": "gameStart",
            "searchDirection": "ASC",
            "numberOfResults": self.HISTORY_PAGE_SIZE,
            "page": page,
            "status": "COMPLETE",
            "gameStartMin": self.config.games_created_after,
            player_field: self.config.bot_player,
        }
        result = self.client.wrap_search_game_history(**query)
        games = result.get("games", [])
        new_ids = 0
        for game in games:
          game_id = int(game["gameId"])
          if game_id not in games_by_id:
            new_ids += 1
          games_by_id[game_id] = game
        if len(games) < self.HISTORY_PAGE_SIZE or not new_ids:
          break
        page += 1
    return [games_by_id[game_id] for game_id in sorted(games_by_id)]

  def _uniqueness_key(self, player, button, game_id):
    if self.config.button_uniqueness == "global":
      return (button.casefold(),)
    if self.config.button_uniqueness == "player":
      return (player.casefold(), button.casefold())
    return (game_id,)

  def entry_games(self, history):
    gate_button = self.config.fights[0].opponent_button
    candidates = []
    for game in history:
      participants = history_match(
          game, self.config.bot_player, gate_button)
      if participants:
        player, _bot = participants
        candidates.append((game, player[0], player[1]))

    accepted = []
    ineligible = []
    first_games = {}
    for game, player, button in sorted(
        candidates, key=lambda item: int(item[0]["gameId"])):
      game_id = int(game["gameId"])
      if (self.config.submissions_close_at is not None and
          int(game["gameStart"]) >= self.config.submissions_close_at):
        ineligible.append(IneligibleGame(
            game_id, player, button,
            "submissions were closed when this game began"))
        continue
      key = self._uniqueness_key(player, button, game_id)
      if key in first_games:
        ineligible.append(IneligibleGame(
            game_id, player, button,
            f"duplicate attempt; [game={first_games[key]}] is authoritative"))
        continue
      first_games[key] = game_id

      if int(game["targetWins"]) != self.config.entry_target_wins:
        ineligible.append(IneligibleGame(
            game_id, player, button,
            f"entry games must be first to {self.config.entry_target_wins}"))
        continue
      eligibility = self.button_filter.eligibility(button)
      if eligibility is None:
        reason = "button is not an exact published production button"
      else:
        reason = eligibility_rejection_reason(
            eligibility, self.config, self.button_filter)
      if reason:
        ineligible.append(IneligibleGame(game_id, player, button, reason))
        continue
      accepted.append((game, player, eligibility.name))
    return accepted, ineligible

  def _matching_completed_fight(
      self, history, quest, fight_number, previous_game_id):
    definition = self.config.fights[fight_number - 1]
    matches = []
    for summary in history:
      participants = history_match(
          summary, self.config.bot_player, definition.opponent_button)
      if not participants:
        continue
      player, _bot = participants
      if (player[0].casefold() != quest.player.casefold() or
          player[1].casefold() != quest.button.casefold() or
          int(summary["targetWins"]) != self.config.entry_target_wins):
        continue
      game = self.client.wrap_load_game_data(int(summary["gameId"]))
      if (game_description_matches(
          self.config, fight_number, quest.source_post_id,
          game.get("description", "")) and
          int(game.get("previousGameId") or 0) == previous_game_id):
        matches.append(summary)
    return min(matches, key=lambda game: int(game["gameId"])) if matches else None

  def _matching_available_fight(
      self, available_games, quest, fight_number, previous_game_id):
    definition = self.config.fights[fight_number - 1]
    matches = []
    for summary in available_games:
      if (summary["opponentName"].casefold() != quest.player.casefold() or
          summary["myButtonName"].casefold() !=
          definition.opponent_button.casefold() or
          summary["opponentButtonName"].casefold() !=
          quest.button.casefold() or
          int(summary["nTargetWins"]) != self.config.entry_target_wins):
        continue
      game = self.client.wrap_load_game_data(int(summary["gameId"]))
      if (game_description_matches(
          self.config, fight_number, quest.source_post_id,
          game.get("description", "")) and
          int(game.get("previousGameId") or 0) == previous_game_id):
        matches.append(summary)
    return min(matches, key=lambda game: int(game["gameId"])) if matches else None

  def _create_chained_fight(self, quest, fight_number, previous_game_id):
    definition = self.config.fights[fight_number - 1]
    description = mission_game_description(
        self.config, fight_number, quest.source_post_id)
    result = self.client.wrap_create_game(
        definition.opponent_button, quest.button, self.config.bot_player,
        quest.player, description, max_wins=self.config.entry_target_wins,
        previous_game_id=previous_game_id)
    game_id = int(result["gameId"])
    LOGGER.info(
        "Adventure advanced: adventure=%s player=%s button=%s fight=%s "
        "game=%s previous_game=%s", self.config.slug, quest.player,
        quest.button, fight_number, game_id, previous_game_id)
    return FightLog(fight_number, game_id, definition.label, "active")

  def rebuild_quest(self, gate_game, player, button, history, available_games):
    gate_won = history_player_won(gate_game, player)
    first = FightLog(
        1, int(gate_game["gameId"]), self.config.fights[0].label,
        "won" if gate_won else "lost")
    quest = QuestLog(
        0, first.game_id, player, button,
        "active" if gate_won else "failed", (first,))
    if not gate_won:
      return quest

    previous_game_id = first.game_id
    for fight_number in range(2, self.config.fight_count + 1):
      completed = self._matching_completed_fight(
          history, quest, fight_number, previous_game_id)
      if completed:
        won = history_player_won(completed, player)
        fight = FightLog(
            fight_number, int(completed["gameId"]),
            self.config.fights[fight_number - 1].label,
            "won" if won else "lost")
        quest = replace(quest, fights=quest.fights + (fight,))
        if not won:
          return replace(quest, status="failed")
        previous_game_id = fight.game_id
        continue

      active = self._matching_available_fight(
          available_games, quest, fight_number, previous_game_id)
      if active:
        fight = FightLog(
            fight_number, int(active["gameId"]),
            self.config.fights[fight_number - 1].label, "active")
      else:
        fight = self._create_chained_fight(
            quest, fight_number, previous_game_id)
      return replace(quest, fights=quest.fights + (fight,))
    return replace(quest, status="survived")

  def _check_once_locked(self):
    self.button_filter.reload()
    # Read live games before history: a game that ends between the two reads
    # is then in history, instead of in neither list and created again.
    available = self.client.wrap_load_active_games()
    available.extend(self.client.wrap_load_new_games())
    history = self.load_history()
    accepted, ineligible = self.entry_games(history)
    quests = [
        self.rebuild_quest(game, player, button, history, available)
        for game, player, button in accepted
    ]
    posts = self.client.wrap_load_forum_thread(self.config.thread_id)["posts"]
    self.update_history_leaderboard(quests, ineligible, posts)
    return completed_game_adventure_can_retire(
        self.config, quests, available)

  def update_history_leaderboard(self, quests, ineligible, posts):
    post = next((item for item in posts if int(item["postId"]) ==
                 self.config.leaderboard_post_id), None)
    if post is None:
      raise ValueError(
          f"Leaderboard post {self.config.leaderboard_post_id} is not in "
          f"thread {self.config.thread_id}")
    entries = build_leaderboard(
        quests, self.client.wrap_load_game_data, include_active=True)
    previous_body = post.get("body", "")
    body = render_leaderboard(
        entries, self.config,
        ineligible if self.config.show_ineligible_games else (),
        previous_body=previous_body)
    if not leaderboard_content_equal(previous_body, body):
      self.client.wrap_edit_forum_post(self.config.leaderboard_post_id, body)
      LOGGER.info(
          "Leaderboard rewritten: adventure=%s entries=%s ineligible=%s",
          self.config.slug, len(entries), len(ineligible))


def parse_args(argv=None):
  parser = argparse.ArgumentParser(description=__doc__)
  parser.add_argument("adventure_config", type=Path)
  parser.add_argument("--credentials", type=Path)
  parser.add_argument("--site")
  parser.add_argument("--buttonmen-tools", type=Path)
  parser.add_argument("--sleep-seconds", type=int)
  parser.add_argument("--log-file", type=Path)
  parser.add_argument("--debug", action="store_true")
  parser.add_argument(
      "--validate-config", action="store_true",
      help="validate local configuration and exit without logging in")
  return parser.parse_args(argv)


def configure_logging(log_file=None, debug=False):
  handlers = [logging.StreamHandler()]
  if log_file:
    handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
  logging.basicConfig(
      level=logging.DEBUG if debug else logging.INFO,
      format="%(asctime)s %(levelname)s %(name)s: %(message)s",
      handlers=handlers,
  )


def main(argv=None):
  args = parse_args(argv)
  config = load_config(args.adventure_config)
  if args.credentials:
    config = replace(config, credentials_file=args.credentials.resolve())
  if args.site:
    config = replace(config, credentials_site=args.site)
  if args.buttonmen_tools:
    config = replace(config, buttonmen_tools_dir=args.buttonmen_tools.resolve())
  if args.sleep_seconds:
    config = replace(config, sleep_seconds=args.sleep_seconds)
  if args.validate_config:
    button_filter = ButtonFilterPublication(config.buttonmen_tools_dir)
    validate_opponents(config, button_filter)
    print(
        f"Valid adventure config: {config.name} ({config.slug}), "
        f"thread {config.thread_id}, {config.fight_count} fights, "
        f"entry mode={config.entry_mode}, "
        f"button uniqueness={config.button_uniqueness}")
    return
  configure_logging(args.log_file, args.debug)
  client = bmutils.BMClientParser(
      str(config.credentials_file), config.credentials_site)
  button_filter = ButtonFilterPublication(config.buttonmen_tools_dir)
  monitor_class = (
      GameHistoryAdventureMonitor
      if config.entry_mode == "completed_game" else AdventureMonitor)
  monitor_class(client, button_filter, config).run_forever()


if __name__ == "__main__":
  main()
