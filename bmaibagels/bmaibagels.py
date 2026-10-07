#!/usr/bin/env python3
import argparse
import json
import random
import traceback
from os import listdir
from subprocess import Popen, PIPE, SubprocessError, check_output
from bmaipy import (BMAI, BMAIR_CAPABILITY_GATED_SKILLS, BUTTON_SPECIALS,
                    RANDOMBM_POOL_BUTTONS, RANDOMBM_SKILL_POOL,
                    bmai_supported_skills, bmai_unsupported_buttons)

import fortune
from func_timeout import func_set_timeout, FunctionTimedOut

import pathlib
from datetime import datetime, timezone

import bmutils
import game_data
import monitor
from bmapi import BMNetworkError
from bmair_release import resolve_bmair

# are warrior dice not working well?

# the bot used to not consider your already-set-swing dice when it was alowed to adjust its own. fixed
# focus doesn't work when BMAI is running on linux for some reason.

always_odds = [item.lower() for item in ["Bagels", "AnnoDomini", "ElihuRoot"]]
ODDS_REPORT_SIMS = 1000
# debug_chat = [item.lower() for item in ["Bagels"]]
debug_chat = []
GAME_DEBUG_DIR = pathlib.Path(__file__).resolve().parent / "game-debug"


def is_loaded_game(game):
  """Return whether this is full game data rather than a game-list summary."""
  return "player" in game and "opponent" in game


def recipe_skills(recipe):
  """Return the skill names named by a ButtonWeavers recipe string."""
  return {bmutils.SkillName[token] for token in recipe or "" if token in bmutils.SkillName}


def bmair_capabilities(binary):
  """Return this BMAIR executable's --capabilities document, or {}."""
  try:
    capabilities = json.loads(check_output(
        [binary, "--capabilities"], text=True, timeout=10))
  except (OSError, SubprocessError, ValueError):
    return {}
  return capabilities if isinstance(capabilities, dict) else {}


def supports_selected_move_reporting(binary, capabilities=None):
  """Return whether this BMAIR executable advertises report_sims."""
  if capabilities is None:
    capabilities = bmair_capabilities(binary)
  return "report_sims" in capabilities.get("commands", [])


def advertised_specials(capabilities):
  """Return the button special IDs this BMAIR binary can apply."""
  if "special" not in capabilities.get("commands", []):
    return frozenset()
  return frozenset(
      special.get("id") for special in capabilities.get("button_specials", []))


def supports_montecarlo_setting(capabilities, name):
  """Return whether this BMAIR's Monte Carlo engine takes the setting."""
  return any(
      engine.get("name") == "montecarlo" and name in engine.get("settings", [])
      for engine in capabilities.get("engines", []))


def supported_skills(capabilities):
  """Return the skills BMAIBagels accepts with this BMAIR capability set.

  A skill the binary reports as parsing-only is accepted on the wire but has
  no game mechanics, so BMAIR would misjudge every game that uses it.
  """
  advertised = set(capabilities.get("skills", []))
  parsing_only = set(capabilities.get("parsing_only_skills", []))
  accepted = bmai_supported_skills | (BMAIR_CAPABILITY_GATED_SKILLS & advertised)
  return accepted - parsing_only



def parse_args(argv=None):
  parser = argparse.ArgumentParser()
  parser.add_argument(
      "-b",
      "--binary",
      help="path to a BMAIR binary; disables managed release updates",
      type=str,
      default=None,
  )
  parser.add_argument(
      "--bmair-update",
      help="managed BMAIR release update policy",
      choices=["auto", "never", "force"],
      default="auto",
  )
  parser.add_argument(
      "-c",
      "--config",
      help="config file containing site parameters",
      type=str,
      default=".bmrc",
  )
  parser.add_argument(
      "-s",
      "--site",
      help="name of config section within config file that defines what buttonweavers site to access",
      type=str)
  parser.add_argument(
      "-f",
      "--filter",
      help="filter out games",
      type=str,
      default="all",
      choices=["all", "odd", "even"],
  )
  parser.add_argument(
    "--sort",
    help="sort active games; waiting handles the longest-waiting game first",
    default="waiting",
    type=str,
    choices=["waiting", "shuffle", "desc", "asc"],
  )
  parser.add_argument(
      "-g",
      "--gameid",
      help="game to run AI against. may make multiple moves against game but will not monitor for other games to play",
      type=int)
  parser.add_argument(
      "-p",
      "--ply",
      help="set AI ply (lookahead)",
      type=int,
      default=game_data.DEFAULT_PLY,
      choices=[1, 2, 3, 4, 5],
  )
  parser.add_argument(
      "--zzz",
      help="fallback sleep time after the faster polling sequence",
      type=int,
      default=120,
  )
  parser.add_argument(
      "--count",
      help="how many games to handle before getting a fresh game list",
      type=int,
      default=-1,
  )
  parser.add_argument(
      "--decision-log",
      help="directory to save every BMAIR input and output in, one pair per decision",
      type=pathlib.Path,
      default=None,
  )
  return parser.parse_args(argv)


class TranscriptReader:
  """Reads a stream while keeping every line read from it."""

  def __init__(self, stream):
    self.stream = stream
    self.lines = []

  def readline(self):
    line = self.stream.readline()
    self.lines.append(line)
    return line

  def __iter__(self):
    return self

  def __next__(self):
    line = self.readline()
    if not line:
      raise StopIteration
    return line

  def text(self):
    return "".join(self.lines)


class BMAIBagels(object):

  def __init__(self,
               client: bmutils.BMClientParser,
               ply,
               binary,
               filter="all",
               sort="asc",
               count=-1,
               sleep_sec=120,
               decision_log=None):
    self.client = client
    self.monitor = monitor.Monitor(
        self.client,
        sleep_sec=sleep_sec,
        initial_sleep_delays=[30, 30, 30, 30, 60, 60],
    )
    self.game_data = game_data.GameData(self.client)
    # pre-seed some particularly bad games with long execution times
    # TODO: someday we need to dive into why these are grumpy, one IS trip related
    self.bad_games = [] # [94507, 93506]
    self.buttons = []
    self.filter = filter
    self.sort = sort
    self.bmai = BMAI()
    self.utils = SomeUtils()
    self.ply = ply
    self.binary = binary
    capabilities = bmair_capabilities(binary)
    self.supports_selected_move_reporting = supports_selected_move_reporting(
        binary, capabilities)
    self.supported_skills = supported_skills(capabilities)
    self.specials = advertised_specials(capabilities)
    # Older BMAIR rejects endgame, so only binaries that list it receive it.
    self.endgame = supports_montecarlo_setting(capabilities, "endgame")
    self.count = count
    self.decision_log = decision_log

  def start_monitor(self):
    self.monitor.start(
        handle_active=self.monitor_handler,
        handle_new=self.new_challenge,
        await_confirm=False,
        sort=self.sort,
        filter=self.filter,
        max=self.count,
    )

  def get_disallowed_skills(self, game):

    if len(self.buttons) == 0:
      self.buttons = self.client.wrap_load_button_names()
    supportset = set()

    if "myButtonName" in game:
      supportset.update(
        self.buttons[game["myButtonName"]]["dieTypes"] +
        self.buttons[game["myButtonName"]]["dieSkills"])
    if "opponentButtonName" in game:
      supportset.update(
        self.buttons[game["opponentButtonName"]]["dieTypes"] +
        self.buttons[game["opponentButtonName"]]["dieSkills"])
    if "player" in game:
      supportset.update(
        self.buttons[game["player"]["button"]["name"]]["dieTypes"] +
        self.buttons[game["player"]["button"]["name"]]["dieSkills"])
      for die in game["player"]["activeDieArray"]:
        supportset.update(die["skills"])
      # Generated buttons such as RandomBM list no skills of their own.
      supportset.update(recipe_skills(game["player"]["button"].get("recipe")))
    if "opponent" in game:
      supportset.update(
        self.buttons[game["opponent"]["button"]["name"]]["dieTypes"] +
        self.buttons[game["opponent"]["button"]["name"]]["dieSkills"])
      for die in game["opponent"]["activeDieArray"]:
        supportset.update(die["skills"])
      supportset.update(recipe_skills(game["opponent"]["button"].get("recipe")))


    # gameSkillsInfo includes some non-skills like RandomBMDuoskill :-(
    # supportset = supportset.update(game["gameSkillsInfo"].keys())

    return supportset - getattr(self, "supported_skills", bmai_supported_skills)

  @staticmethod
  def get_disallowed_buttons(game, specials=frozenset(),
                             skills=bmai_supported_skills):
    """Return explicitly unsupported buttons present in a game summary or game.

    Random buttons are refused when a challenge arrives unless the binary
    supports every skill they can draw, because only the summary is
    available then. Once a game is active its recipes have been
    generated, so get_disallowed_skills checks the dice it actually rolled
    and the random-button name no longer blocks it.
    """
    unsupported = set(bmai_unsupported_buttons) | {
        button for button, needed in BUTTON_SPECIALS.items()
        if not set(needed) <= specials}
    if not RANDOMBM_SKILL_POOL <= set(skills):
      unsupported |= RANDOMBM_POOL_BUTTONS
    if is_loaded_game(game):
      unsupported = {
          name for name in unsupported if not name.startswith("RandomBM")}

    button_names = {
        game.get("myButtonName"),
        game.get("opponentButtonName"),
        game.get("player", {}).get("button", {}).get("name"),
        game.get("opponent", {}).get("button", {}).get("name"),
    }
    button_names.discard(None)
    return button_names & unsupported

  def new_challenge(self, game):
    # TODO: consider making sure the bot can even make the first move before accepting the game
    gameid = game["gameId"]
    try:
      disallowedset = self.get_disallowed_skills(game)
      disallowedbuttons = self.get_disallowed_buttons(
          game, getattr(self, "specials", frozenset()),
          getattr(self, "supported_skills", bmai_supported_skills))
      if disallowedbuttons:
        print(f"Not accepting games with unsupported buttons: {disallowedbuttons}")
        action = "reject"
      elif len(disallowedset) > 0:
        print(f"Not accepting games with the following: {disallowedset}")
        action = "reject"
      else:
        action = "accept"

      return self.client.wrap_react_to_new_game(gameid, action)
    except BMNetworkError as error:
      print(
          f"Network error handling new game {gameid}; leaving it pending "
          f"for a later poll: {error}")
      return False

  def monitor_handler(self, game, calc_other_side=False):
    gameid = game["gameId"]
    if gameid in self.bad_games:
      return False

    try:
      game = self.game_data.fetch(gameid)
    except BMNetworkError as error:
      print(
          f"Network error loading game {gameid}; leaving it available for "
          f"a later poll: {error}")
      return False

    if game["gameState"] in ["END_GAME", "CANCELLED", "DETERMINE_INITIATIVE"]:
      print(f"game {gameid} state is {game['gameState']}")
      return True

    if game["gameState"] == "ADJUST_FIRE_DICE":
      # Cancel an interrupted two-step Fire attack before recalculating it.
      try:
        retval = self.client.client.adjust_fire_dice(
            gameid,
            "cancel",
            [],
            [],
            roundNumber=game["roundNumber"],
            timestamp=game["timestamp"],
        )
      except BMNetworkError as error:
        print(
            f"Network error cancelling pending Fire attack in game {gameid}; "
            f"leaving it available for a later poll: {error}")
        return False
      print(retval.message)
      if retval.status != "ok":
        return False
      return self.monitor_handler(game)

    if not game["player"]["waitingOnAction"] and not calc_other_side:
      # may have come in recursivly and we need to break away if its not actually our turn
      print(f"not my turn in game {gameid}")
      return True

    try:
      disallowedset = self.get_disallowed_skills(game)
      disallowedbuttons = self.get_disallowed_buttons(
          game, getattr(self, "specials", frozenset()),
          getattr(self, "supported_skills", bmai_supported_skills))
    except BMNetworkError as error:
      print(
          f"Network error checking game {gameid}; leaving it available for "
          f"a later poll: {error}")
      return False
    if disallowedbuttons:
      msg=f"game {gameid} has unsupported buttons: {disallowedbuttons}"
      print(msg)
      self.bad_game(gameid, game_data.bmai.dump(game), f"rejected before execution: {msg}")
      return False
    if len(disallowedset) > 0:
      msg=f"game {gameid} has disallowed skills: {disallowedset}"
      print(msg)
      self.bad_game(gameid, game_data.bmai.dump(game), f"rejected before execution: {msg}")
      return False

    if calc_other_side:

      if game["gameState"] not in ["START_TURN"]:
        print(f"game {gameid} is at {game['gameState']}, dont waste time calculating other side")
        return True

      if game["player"]["waitingOnAction"]:
        print(f"game {gameid} is waiting on action, no time to calculate other side")
        return True
      else:
        print("calculating the other side")

    else:
      print(f"{game['gameId']}: {game['player']['playerName']} ({game['player']['button']['name']})  vs. {game['opponent']['playerName']} ({game['opponent']['button']['name']})")

    can_check_other_odds = False

    # BMAIR rejects ply 0, so fall back no further than ply 1.
    for ply in range(self.ply, 0, -1):
      if ply != self.ply:
        print(f"trying ply {ply}")
      report_sims = (ODDS_REPORT_SIMS if
          getattr(self, "supports_selected_move_reporting", False) and
          self.should_report_odds(game, calc_other_side) else 0)
      bmai_input = game_data.bmai.dump(
          game, ply=ply, report_sims=report_sims,
          specials=bool(getattr(self, "specials", frozenset())),
          endgame=getattr(self, "endgame", False))
      try:
        can_check_other_odds = self.exec_bmai(
            bmai_input,
            game=game,
            state=game["gameState"],
            other_odds=calc_other_side,
        )
        break
      except FunctionTimedOut:
        print(f"timed out gameId={game['gameId']} ply={ply}")
        if ply == 1:
          self.bad_game(game["gameId"], bmai_input, f"started at ply={self.ply} and reached ply={ply} while still timing out")
          return False
      except BMNetworkError as error:
        print(
            f"Network error while playing game {gameid}; not marking the "
            f"game bad and deferring it to a later poll: {error}")
        return False
      except BaseException as e:
        ## some other problem
        message = getattr(e, 'message', repr(e))
        print(f"Exception {message}")
        if "You can't edit the requested chat message now" in message:
          break
        traceback.print_tb(e.__traceback__)
        self.bad_game(game["gameId"], bmai_input, message)
        return False

    # if we have been calcing the other side then we need to be DONE!!!
    if calc_other_side:
      return True

    # lets immediately try to go again
    # to quickly address the situations where we won initiative
    # or the other player was forced to pass
    # also try to calculate the new odds after a re-roll
    return self.monitor_handler(game, calc_other_side=can_check_other_odds)

  @func_set_timeout(60 * 60)  #
  def exec_bmai(self, input, game, state, other_odds=False):
    log_prefix = self.log_decision_input(game["gameId"], input)
    bmai = Popen([self.binary],
                 stdin=PIPE,
                 stdout=PIPE,
                 stderr=PIPE,
                 universal_newlines=True)
    output = TranscriptReader(bmai.stdout)
    try:
      return self._exec_bmai(bmai, output, input, game, state, other_odds)
    finally:
      self.log_decision_output(log_prefix, output.text())

  def _exec_bmai(self, bmai, output, input, game, state, other_odds):
    bmai.stdin.write(input)
    bmai.stdin.flush()

    banner = self.read_bmair_banner(output)

    acted = False
    printed = False
    can_check_other_odds = False
    win_odds = None
    stats = None
    problem = None

    for line in output:
      if (" p0 best move " in line or
          " p0 selected move report " in line) and "%" in line:
        win_odds = line.split("%")[0].split()[-1]
      if line.startswith("stats "):
        stats = line
      if "err" in line or "fail" in line:
        print(line, end="")
        problem = line
        printed = True
      if not other_odds and "action" in line:
        if state == "SPECIFY_DICE":
          # TODO swing/opt better
          swings = len(game["player"]["swingRequestArray"])
          opts = len(game["player"]["optRequestArray"])
          swing_select = []
          opt_select = []
          for s in range(swings + opts):
            l = output.readline().strip()
            if l.startswith("swing"):
              swing_select.append(l)
            elif l.startswith("option"):
              opt_select.append(l)
          acted = self.submit_swings(game, swing_select, opt_select)
          continue
        elif state == "CHOOSE_RESERVE_DICE":
          l = output.readline().strip()
          acted = self.submit_reserve(game, l)
          continue
        elif state == "CHOOSE_AUXILIARY_DICE":
          l = output.readline().strip()
          acted = self.submit_auxiliary(game, l)
          continue
        elif state == "START_TURN":
          atk_type = output.readline().strip()
          source_dice = output.readline().strip()
          target_dice = output.readline().strip()
          turbos = self.attacking_turbos(game, source_dice)
          turbo_select, fire_select = self.attack_adjustments(
              output, game, turbos)
          (isok, can_check_other_odds, atk_resp) = self.submit_attack(
              game,
              atk_type,
              source_dice,
              target_dice,
              turbo_select=turbo_select,
              fire_select=fire_select,
              banner=banner,
              win_odds=win_odds,
              stats=stats,
          )
          if isok:
            acted = True
          else:
            problem = f"atk_type={atk_type} source_dice={source_dice} target_dice={target_dice}\nturbo_select={turbo_select}\nfire_select={fire_select}\nisok={isok} atk_resp={atk_resp}"
            print(problem)
          continue
        elif state == "REACT_TO_INITIATIVE":
          action = output.readline().strip()
          acted = self.react_initiative(game, action)
          continue
    if printed:
      print("")
    if not acted and other_odds and win_odds is not None:
      new_odds = "%0.1f" % (100 - float(win_odds))
      print(f"other_odds={other_odds} win_odds={win_odds} new_odds={new_odds}")
      (chat, x) = self.determine_chat(
          game, None, win_odds=new_odds, other_odds=True)
      lastchatlog = game["gameChatLog"][0]
      chat = lastchatlog["message"] + "\nedit: " + chat
      retval = self.client.wrap_submit_chat(game["gameId"], chat, edit_timestamp=lastchatlog["timestamp"])
    if not acted and not other_odds:
      print("¯\\_(ツ)_/¯")
      self.bad_game(game["gameId"], input, f"no action taken\n¯\\_(ツ)_/¯\n{problem}")
    bmai.stdin.flush()
    bmai.stdin.close()
    bmai.stdout.flush()
    bmai.stdout.close()
    bmai.stderr.flush()
    bmai.stderr.close()
    return can_check_other_odds

  def submit_swings(self, game, swing_select, opt_select):
    swing_array = dict()
    opt_array = dict()
    for swing in swing_select:
      parts = swing.split(" ")
      swing_array[parts[1]] = parts[2]
    for opt in opt_select:
      parts = opt.split(" ")
      opt_array[parts[1]] = parts[2]

    retval = self.client.client.submit_die_values(
        game["gameId"],
        swingArray=swing_array,
        optionArray=opt_array,
        roundNumber=game["roundNumber"],
        timestamp=game["timestamp"],
    )
    print(retval.message)
    return retval.status == "ok"

  def react_initiative(self, game, action):
    idx = []
    val = []
    if action == "pass":
      action = "decline"
    else:
      parts = action.split()
      action = parts[0]
      idx.append(parts[1])
      if len(parts) > 2:
        val.append(parts[2])
    retval = self.client.client.react_to_initiative(
        game["gameId"],
        action,
        idx,
        val,
        roundNumber=game["roundNumber"],
        timestamp=game["timestamp"],
    )
    print(retval.message)
    if "did not turn your focus dice down far enough" in retval.message:
      # BMAI sometimes does this sometimes and will get stuck
      # currently i dont have a loop to retrigger BMAI
      # and if i did i dont have a way of telling BMAI to produce something
      # different with the same inputs
      # for now, decline
      retval = self.client.client.react_to_initiative(
          game["gameId"],
          "decline",
          [],
          [],
          roundNumber=game["roundNumber"],
          timestamp=game["timestamp"],
      )
      print(retval.message)
    return retval.status == "ok"

  def submit_reserve(self, game, reserve_cmd):
    die_idx = reserve_cmd.split()[1]
    if die_idx == "-1":
      retval = self.client.client.choose_reserve_dice(game["gameId"], "decline")
    else:
      retval = self.client.client.choose_reserve_dice(game["gameId"], "add",
                                                      die_idx)

    print(retval.message)
    return retval.status == "ok"

  def submit_auxiliary(self, game, auxiliary_cmd):
    die_idx = auxiliary_cmd.split()[1]
    if die_idx == "-1":
      retval = self.client.client.choose_auxiliary_dice(game["gameId"],
                                                        "decline")
    else:
      retval = self.client.client.choose_auxiliary_dice(game["gameId"], "add",
                                                        die_idx)

    print(retval.message)
    return retval.status == "ok"

  def submit_attack(
      self,
      game,
      type,
      source,
      target,
      turbo_select,
      fire_select=None,
      banner=None,
      win_odds=None,
      stats=None,
  ):
    my_idx = game["activePlayerIdx"]
    their_idx = 0 if my_idx == 1 else 1
    die_selects = self._generate_attack_array(game, my_idx, their_idx,
                                              source.split(" "),
                                              target.split(" "))
    turbo_array = self.turbo_values(turbo_select)

    (chat, can_check_other_odds) = self.determine_chat(game, banner, win_odds,
                                                       stats)

    retval = self.client.client.submit_turn(
        game["gameId"],
        my_idx,
        their_idx,
        dieSelectStatus=die_selects,
        attackType=type.capitalize(),
        timestamp=game["timestamp"],
        roundNumber=game["roundNumber"],
        turboVals=turbo_array,
        chat=chat,
    )
    if retval.status != "ok":
      return False, can_check_other_odds, retval.message

    # ButtonWeavers pauses in ADJUST_FIRE_DICE when Fire is required ("must
    # turn down fire dice to complete this attack") and, with the Fire
    # overshooting preference, when it is optional ("must decide whether to
    # turn down fire dice"). Both need an adjustFire reply, or the next poll
    # finds the paused attack and cancels it.
    if "turn down fire dice" in retval.message.lower():
      current = self.client.client.load_game_data(game["gameId"])
      if current.status != "ok":
        return False, can_check_other_odds, current.message
      if current.data["gameState"] != "ADJUST_FIRE_DICE":
        return False, can_check_other_odds, (
            "submitTurn requested Fire adjustment, but loadGameData returned "
            f"{current.data['gameState']}")

      fire_select = fire_select or {}
      action = "turndown" if fire_select else "no_turndown"
      fire_retval = self.client.client.adjust_fire_dice(
          game["gameId"],
          action,
          list(fire_select),
          list(fire_select.values()),
          roundNumber=current.data["roundNumber"],
          timestamp=current.data["timestamp"],
      )
      return (
          fire_retval.status == "ok",
          can_check_other_odds,
          fire_retval.message,
      )

    return True, can_check_other_odds, retval.message

  @staticmethod
  def read_bmair_banner(output):
    """Read BMAIR's title, two copyright lines, and version line."""
    title = output.readline()
    rust_copyright = output.readline()
    original_bmai_copyright = output.readline()
    version = output.readline()
    return title + rust_copyright + original_bmai_copyright + version

  @staticmethod
  def attacking_turbos(game, source_dice):
    """Return Turbo choices for dice participating in the chosen attack.

    ButtonMen sometimes serializes contiguous die-index keys as a JSON list.
    Those list positions are already zero-based active-die indices.  The site
    accepts Turbo values only for attacking dice, so exclude every other Turbo
    die before consuming BMAI's output lines.
    """
    turbos = game["player"]["turboSizeArray"]
    if isinstance(turbos, list):
      turbos = dict(enumerate(turbos))

    attacker_indices = {int(index) for index in source_dice.split()}
    return {
        key: turbos[key] for key in sorted(turbos, key=int)
        if int(key) in attacker_indices
    }

  @staticmethod
  def turbo_values(turbo_select):
    values = dict()
    for k, selection in turbo_select.items():
      if selection['line']:
        values[k] = selection['line'].split(" ")[2]
      else:
        values[k] = str(selection['value'])
    return values

  @staticmethod
  def fire_values(output):
    """Read BMAIR's trailing ``fire DIE VALUE`` attack selections."""
    values = {}
    for line in output:
      parts = line.strip().split()
      if not parts:
        continue
      if len(parts) != 3 or parts[0] != "fire":
        raise ValueError(f"Unexpected BMAIR attack output: {line.strip()}")
      die_idx, value = parts[1:]
      if die_idx in values:
        raise ValueError(f"Duplicate BMAIR Fire selection for die {die_idx}")
      values[die_idx] = value
    return values

  @staticmethod
  def attack_adjustments(output, game, turbos):
    """Split BMAIR's optional Turbo and Fire lines after an attack."""
    turbo_lines = []
    fire_lines = []
    for line in output:
      stripped = line.strip()
      if not stripped:
        continue
      if stripped.startswith(("option ", "swing ")):
        turbo_lines.append(stripped)
      elif stripped.startswith("fire "):
        fire_lines.append(stripped)
      else:
        raise ValueError(f"Unexpected BMAIR attack output: {stripped}")

    if len(turbo_lines) > len(turbos):
      raise ValueError("BMAIR returned more Turbo choices than attacking Turbo dice")

    turbo_select = {}
    for position, (key, allowed) in enumerate(turbos.items()):
      turbo_select[key] = {
          "array": allowed,
          "line": turbo_lines[position] if position < len(turbo_lines) else "",
          "value": game["player"]["activeDieArray"][int(key)]["sides"],
      }
    return turbo_select, BMAIBagels.fire_values(fire_lines)

  def _generate_attack_array(self, game, my_idx, their_idx, attackers,
                             defenders):
    attack = {}
    for i in range(len(game["playerDataArray"][my_idx]["activeDieArray"])):
      attack[f"playerIdx_{my_idx:d}_dieIdx_{i:d}"] = (True if str(i)
                                                      in attackers else False)
    for i in range(len(game["playerDataArray"][their_idx]["activeDieArray"])):
      attack[f"playerIdx_{their_idx:d}_dieIdx_{i:d}"] = (
          True if str(i) in defenders else False)
    return attack

  def determine_chat(self,
                     game,
                     banner,
                     win_odds=None,
                     stats=None,
                     other_odds=False):

    debug = f" @ {game['gameState']}" if game["opponent"]["playerName"].lower() in debug_chat else ""
    
    sorted_chat = sorted(game["gameChatLog"], key=lambda x: x["timestamp"])

    bot_has_talked = False
    opponent_needs_reply = False

    opponent_last_chat_time = 0
    opponent_last_chat_mesg = ""
    bot_last_chat_time = 0
    bot_last_chat_mesg = ""

    for c in sorted_chat:
      if c["player"] == self.client.username:
        bot_last_chat_time = max(bot_last_chat_time, c["timestamp"])
        bot_last_chat_mesg = c["message"]
      else:
        opponent_last_chat_time = max(opponent_last_chat_time, c["timestamp"])
        opponent_last_chat_mesg = c["message"]

    if bot_last_chat_time > 0:
      bot_has_talked = True
    if opponent_last_chat_time > bot_last_chat_time:
      opponent_needs_reply = True

    retval = None
    if other_odds and f"chance {self.client.username} wins" in bot_last_chat_mesg:
      retval = f"{win_odds}% chance {self.client.username} wins (after re-roll) {debug}"
    elif not bot_has_talked:
      retval = banner + "\nCOMMANDS: odds, stats"
    elif opponent_needs_reply:
      if opponent_last_chat_mesg.lower().startswith("bad bot"):
        retval = "sorry :-("
      elif opponent_last_chat_mesg.lower().startswith("good bot"):
        retval = "thanks (*^.^*)"
      elif stats is not None and "stats" in opponent_last_chat_mesg.lower():
        retval = stats
      elif win_odds is not None and (
          "win?" in opponent_last_chat_mesg.lower() or
          "odds" in opponent_last_chat_mesg.lower() or
          game["opponent"]["playerName"].lower() in always_odds):
        retval = f"{win_odds}% chance {self.client.username} wins (before re-roll) {debug}"
      else:
        retval = self.utils.get_random_fortune()
    elif game["opponent"]["playerName"].lower() in always_odds:
      retval = f"{win_odds}% chance {self.client.username} wins (before re-roll) {debug}"

    print(f"chat: {retval}")
    if not retval:
      return "", False
    else:
      return retval, (f"chance {self.client.username} wins" in retval and not other_odds)

  def should_report_odds(self, game, other_odds=False):
    """Request the expensive selected-move sample only when chat will use it."""
    if other_odds:
      return True

    chats = sorted(game.get("gameChatLog", []), key=lambda item: item["timestamp"])
    bot_chats = [item for item in chats if item["player"] == self.client.username]
    if not bot_chats:
      return False

    opponent_chats = [
        item for item in chats if item["player"] != self.client.username]
    latest_bot = bot_chats[-1]
    latest_opponent = opponent_chats[-1] if opponent_chats else None
    opponent_name = game["opponent"]["playerName"].lower()
    if latest_opponent is not None and latest_opponent["timestamp"] > latest_bot["timestamp"]:
      message = latest_opponent["message"].lower()
      if (message.startswith("bad bot") or message.startswith("good bot") or
          "stats" in message):
        return False
      return ("win?" in message or "odds" in message or
              opponent_name in always_odds)
    return opponent_name in always_odds

  def log_decision_input(self, game_id, game_input):
    """Save BMAIR's input when --decision-log is on; return the file prefix."""
    if self.decision_log is None:
      return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    prefix = self.decision_log / f"{game_id}-{stamp}"
    try:
      self.decision_log.mkdir(parents=True, exist_ok=True)
      prefix.with_name(prefix.name + "-input.txt").write_text(
          game_input, encoding="utf-8")
    except OSError as e:
      # A full or broken log disk must never stop the bot from playing.
      print(f"decision log failed: {e}")
      return None
    return prefix

  @staticmethod
  def log_decision_output(prefix, output):
    if prefix is None:
      return
    try:
      prefix.with_name(prefix.name + "-output.txt").write_text(
          output, encoding="utf-8")
    except OSError as e:
      print(f"decision log failed: {e}")

  def bad_game(self, game_id, game_input, info=None):
    self.bad_games.append(game_id)
    GAME_DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    (GAME_DEBUG_DIR / f"{game_id}-input.txt").write_text(
        game_input, encoding="utf-8")
    if info is not None:
      (GAME_DEBUG_DIR / f"{game_id}-output.txt").write_text(
          info, encoding="utf-8")


class SomeUtils:

  def get_fortune_file(self):
    current_dir = pathlib.Path(__file__).parent.resolve()
    return f"{current_dir}/fortunes/" + random.choice(listdir(f"{current_dir}/fortunes")).replace(
        ".dat", "")

  def get_random_fortune(self):
    return fortune.get_random_fortune(self.get_fortune_file())


if __name__ == "__main__":
  print("startup fortune test:")
  print(SomeUtils().get_random_fortune())
  args = parse_args()
  print(f"args={args}")
  binary = args.binary
  if binary is None:
    binary = str(resolve_bmair(
        mode=args.bmair_update,
        fallback="./bmai-v3.0-68-g4813530-PR82",
    ))
    print(f"BMAIBagels will use managed BMAIR binary: {binary}")
  else:
    print(f"BMAIBagels will use explicitly configured BMAIR binary: {binary}")
  bmclient = bmutils.BMClientParser(args.config, args.site)
  bmaibagels = BMAIBagels(
    bmclient,
    filter=args.filter,
    sort=args.sort,
    ply=args.ply,
    binary=binary,
    count=args.count,
    sleep_sec=args.zzz,
    decision_log=args.decision_log,
  )
  if args.gameid:
    bmaibagels.monitor_handler({"gameId": args.gameid})
  else:
    bmaibagels.start_monitor()
