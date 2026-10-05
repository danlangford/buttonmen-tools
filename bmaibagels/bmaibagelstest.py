import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock, patch
import requests
from func_timeout import FunctionTimedOut
import bmaibagels as bmaibagels_module
from bmaibagels import (BMAIBagels, SomeUtils, parse_args, supported_skills,
                        supports_selected_move_reporting)
from bmapi import BMClient, BMNetworkError
from bmaipy import (BMAIR_CAPABILITY_GATED_SKILLS, RANDOMBM_SKILL_POOL,
                    bmai_supported_skills, bmai_unsupported_buttons)
from game_data import bmai


class TestSomeUtils(unittest.TestCase):

  def setUp(self):
    self.SomeUtils = SomeUtils()

  def test_random_fortune_file(self):
    filepath = self.SomeUtils.get_fortune_file()
    print(filepath)
    self.assertIsNotNone(filepath)

  def test_random_fortune(self):
    fortune = self.SomeUtils.get_random_fortune()
    print(fortune)
    self.assertIsNotNone(fortune)


class TestBMAIBagels(unittest.TestCase):

  def test_moves_get_a_sixty_second_budget_by_default(self):
    self.assertEqual(60, parse_args([]).time_limit)
    self.assertEqual(0, parse_args(["--time-limit", "0"]).time_limit)

  def test_time_limit_is_sent_only_to_binaries_that_advertise_it(self):
    newer = {"engines": [{"name": "montecarlo", "settings": ["ply", "time_limit"]}]}
    older = {"engines": [{"name": "montecarlo", "settings": ["ply"]}]}
    self.assertTrue(bmaibagels_module.supports_time_limit(newer))
    self.assertFalse(bmaibagels_module.supports_time_limit(older))
    self.assertFalse(bmaibagels_module.supports_time_limit({}))

  def test_default_search_depth_uses_parallelism_friendly_ply_three(self):
    self.assertEqual(3, parse_args([]).ply)

  def test_search_depth_can_still_be_overridden(self):
    self.assertEqual(2, parse_args(["--ply", "2"]).ply)

  def test_longest_waiting_game_is_the_default_sort(self):
    self.assertEqual("waiting", parse_args([]).sort)

  @patch("bmaibagels.check_output")
  def test_selected_move_reporting_is_capability_gated(self, capabilities):
    capabilities.return_value = '{"commands":["getaction","report_sims"]}'
    self.assertTrue(supports_selected_move_reporting("bmair"))
    capabilities.return_value = '{"commands":["getaction"]}'
    self.assertFalse(supports_selected_move_reporting("bmair"))

  def test_selected_move_report_is_requested_only_when_odds_will_be_used(self):
    bagels = BMAIBagels.__new__(BMAIBagels)
    bagels.client = SimpleNamespace(username="BMAIBagels")
    game = {
        "opponent": {"playerName": "SomeoneElse"},
        "gameChatLog": [],
    }
    self.assertFalse(bagels.should_report_odds(game))

    game["gameChatLog"] = [
        {"player": "BMAIBagels", "timestamp": 1, "message": "hello"},
        {"player": "SomeoneElse", "timestamp": 2, "message": "odds please"},
    ]
    self.assertTrue(bagels.should_report_odds(game))

    game["gameChatLog"][-1]["message"] = "good bot"
    self.assertFalse(bagels.should_report_odds(game))
    self.assertTrue(bagels.should_report_odds(game, other_odds=True))

    game["opponent"]["playerName"] = "ElihuRoot"
    game["gameChatLog"].append(
        {"player": "BMAIBagels", "timestamp": 3, "message": "thanks"})
    self.assertTrue(bagels.should_report_odds(game))

  def make_bagels(self):
    bagels = BMAIBagels.__new__(BMAIBagels)
    bagels.client = SimpleNamespace(client=Mock())
    bagels.client.client.choose_auxiliary_dice.return_value = SimpleNamespace(
        status="ok", message="accepted")
    return bagels

  def test_fire_values_parse_trailing_bmair_selections(self):
    self.assertEqual(
        {"1": "2", "4": "7"},
        BMAIBagels.fire_values(StringIO("fire 1 2\nfire 4 7\n")),
    )

  def test_fire_values_reject_unexpected_trailing_output(self):
    with self.assertRaisesRegex(ValueError, "Unexpected BMAIR attack output"):
      BMAIBagels.fire_values(StringIO("warning: incomplete action\n"))

  def test_attack_adjustments_do_not_consume_fire_as_blank_turbo(self):
    game = {
        "player": {
            "activeDieArray": [{"sides": 6}, {"sides": 8}],
        },
    }

    turbo, fire = BMAIBagels.attack_adjustments(
        StringIO("fire 1 2\n"), game, {0: [4, 6, 8]})

    self.assertEqual("", turbo[0]["line"])
    self.assertEqual(6, turbo[0]["value"])
    self.assertEqual({"1": "2"}, fire)

  def test_submit_attack_completes_required_fire_turndown(self):
    bagels = self.make_bagels()
    bagels.determine_chat = Mock(return_value=("", False))
    bagels.client.client.submit_turn.return_value = SimpleNamespace(
        status="ok",
        message="must turn down fire dice to complete this attack",
    )
    bagels.client.client.load_game_data.return_value = SimpleNamespace(
        status="ok",
        message="loaded",
        data={
            "gameState": "ADJUST_FIRE_DICE",
            "roundNumber": 3,
            "timestamp": 200,
        },
    )
    bagels.client.client.adjust_fire_dice.return_value = SimpleNamespace(
        status="ok", message="attack completed")
    game = {
        "gameId": 42,
        "activePlayerIdx": 0,
        "roundNumber": 3,
        "timestamp": 100,
        "playerDataArray": [
            {"activeDieArray": [{}, {}]},
            {"activeDieArray": [{}]},
        ],
    }

    is_ok, _, message = bagels.submit_attack(
        game, "power", "0", "0", {}, {"1": "1"})

    self.assertTrue(is_ok)
    self.assertEqual("attack completed", message)
    bagels.client.client.adjust_fire_dice.assert_called_once_with(
        42,
        "turndown",
        ["1"],
        ["1"],
        roundNumber=3,
        timestamp=200,
    )

  def test_submit_attack_declines_optional_fire_overshoot(self):
    # ButtonWeavers' allows_firing message, sent when the Power attack is
    # already legal and the Fire overshooting preference is on.
    bagels = self.make_bagels()
    bagels.determine_chat = Mock(return_value=("", False))
    bagels.client.client.submit_turn.return_value = SimpleNamespace(
        status="ok",
        message=(
            "BMAIBagels chose to perform a Power attack using [F(8):8] against "
            "[(6):5]; BMAIBagels must decide whether to turn down fire dice"
        ),
    )
    bagels.client.client.load_game_data.return_value = SimpleNamespace(
        status="ok",
        message="loaded",
        data={
            "gameState": "ADJUST_FIRE_DICE",
            "roundNumber": 3,
            "timestamp": 200,
        },
    )
    bagels.client.client.adjust_fire_dice.return_value = SimpleNamespace(
        status="ok", message="attack completed")
    game = {
        "gameId": 42,
        "activePlayerIdx": 0,
        "roundNumber": 3,
        "timestamp": 100,
        "playerDataArray": [
            {"activeDieArray": [{}]},
            {"activeDieArray": [{}]},
        ],
    }

    is_ok, _, _ = bagels.submit_attack(
        game, "power", "0", "0", {}, {})

    self.assertTrue(is_ok)
    bagels.client.client.adjust_fire_dice.assert_called_once_with(
        42,
        "no_turndown",
        [],
        [],
        roundNumber=3,
        timestamp=200,
    )

  def test_submit_attack_applies_optional_fire_overshoot_turndown(self):
    bagels = self.make_bagels()
    bagels.determine_chat = Mock(return_value=("", False))
    bagels.client.client.submit_turn.return_value = SimpleNamespace(
        status="ok",
        message="BMAIBagels must decide whether to turn down fire dice",
    )
    bagels.client.client.load_game_data.return_value = SimpleNamespace(
        status="ok",
        message="loaded",
        data={
            "gameState": "ADJUST_FIRE_DICE",
            "roundNumber": 3,
            "timestamp": 200,
        },
    )
    bagels.client.client.adjust_fire_dice.return_value = SimpleNamespace(
        status="ok", message="attack completed")
    game = {
        "gameId": 42,
        "activePlayerIdx": 0,
        "roundNumber": 3,
        "timestamp": 100,
        "playerDataArray": [
            {"activeDieArray": [{}, {}]},
            {"activeDieArray": [{}]},
        ],
    }

    is_ok, _, _ = bagels.submit_attack(
        game, "power", "0", "0", {}, {"1": "14"})

    self.assertTrue(is_ok)
    bagels.client.client.adjust_fire_dice.assert_called_once_with(
        42,
        "turndown",
        ["1"],
        ["14"],
        roundNumber=3,
        timestamp=200,
    )

  def test_turbo_values_keep_current_size_when_output_is_blank(self):
    turbo_select = {
        "1": {
            "line": "option 1 20",
            "value": 1,
        },
        "2": {
            "line": "",
            "value": 1,
        },
    }

    self.assertEqual({"1": "20", "2": "1"},
                     BMAIBagels.turbo_values(turbo_select))

  def test_reads_both_bmair_copyright_lines(self):
    output = StringIO(
        "BMAIR: the Button Men AI in Rust\n"
        "Rust port Copyright 2026 Dan Langford.\n"
        "Original BMAI Copyright 2001-2026 Denis Papp.\n"
        "Version: 0.5.0\n"
        "action\n"
    )

    banner = BMAIBagels.read_bmair_banner(output)

    self.assertEqual(
        "BMAIR: the Button Men AI in Rust\n"
        "Rust port Copyright 2026 Dan Langford.\n"
        "Original BMAI Copyright 2001-2026 Denis Papp.\n"
        "Version: 0.5.0\n",
        banner,
    )
    self.assertEqual("action\n", output.readline())

  def test_rejects_explicitly_unsupported_random_button(self):
    game = {
        "gameId": 42,
        "myButtonName": "RandomBMMixed",
        "opponentButtonName": "Aylee",
    }

    self.assertEqual(
        {"RandomBMMixed"},
        BMAIBagels.get_disallowed_buttons(game),
    )

  def test_active_random_button_game_is_judged_by_its_rolled_dice(self):
    bagels = self.make_bagels()
    bagels.buttons = {
        "RandomBMDuoskill": {"dieTypes": [], "dieSkills": []},
        "Aylee": {"dieTypes": [], "dieSkills": []},
    }
    bagels.supported_skills = bmai_supported_skills | {"Rush"}

    def loaded_game(recipe, die_skills):
        return {
            "gameId": 94915,
            "player": {
                "button": {"name": "RandomBMDuoskill", "recipe": recipe},
                "activeDieArray": [{"skills": die_skills}],
            },
            "opponent": {
                "button": {"name": "Aylee", "recipe": "(4) (8) (12) (20)"},
                "activeDieArray": [{"skills": []}],
            },
        }

    playable = loaded_game("(8) #(10) h(10) #h(12) (Y)", ["Rush", "Weak"])
    self.assertEqual(set(), BMAIBagels.get_disallowed_buttons(playable))
    self.assertEqual(set(), bagels.get_disallowed_skills(playable))

    # A rolled skill is caught from the recipe even after that die is captured.
    boom = loaded_game("b(8) (10) h(10) (Y)&", ["Weak"])
    self.assertEqual(set(), BMAIBagels.get_disallowed_buttons(boom))
    self.assertEqual({"Boom", "Mad"}, bagels.get_disallowed_skills(boom))

  def test_random_button_summaries_stay_blocked(self):
    summary = {
        "gameId": 119416,
        "myButtonName": "RandomBMMixed",
        "opponentButtonName": "Aylee",
    }
    self.assertEqual({"RandomBMMixed"}, BMAIBagels.get_disallowed_buttons(summary))

  def test_random_button_challenges_are_accepted_once_the_whole_pool_is_supported(self):
    summary = {
        "gameId": 119416,
        "myButtonName": "RandomBMPentaskill",
        "opponentButtonName": "RandomBMMixed",
    }
    all_skills = supported_skills({"skills": ["Boom", "Mad", "Rush"]})
    self.assertEqual(set(), BMAIBagels.get_disallowed_buttons(
        summary, skills=all_skills))
    self.assertEqual({"RandomBMPentaskill", "RandomBMMixed"},
                     BMAIBagels.get_disallowed_buttons(
                         summary, skills=all_skills - {"Boom"}))

  def test_randombm_pool_matches_supported_skill_names(self):
    self.assertLessEqual(RANDOMBM_SKILL_POOL,
                         bmai_supported_skills | BMAIR_CAPABILITY_GATED_SKILLS)

  def test_rush_is_supported_only_when_bmair_advertises_it(self):
    self.assertNotIn("Rush", bmai_supported_skills)
    self.assertNotIn("Rush", supported_skills({"skills": ["Fire", "Rage"]}))
    self.assertNotIn("Rush", supported_skills({}))
    self.assertIn("Rush", supported_skills({"skills": ["Fire", "Rush"]}))
    # Capabilities never enable skills outside the reviewed gated set.
    self.assertNotIn("Wildcard", supported_skills({"skills": ["Wildcard"]}))

  def test_boom_and_mad_are_supported_only_when_bmair_advertises_them(self):
    for skill in ("Boom", "Mad"):
      self.assertNotIn(skill, bmai_supported_skills)
      self.assertNotIn(skill, supported_skills({"skills": ["Rush"]}))
      self.assertIn(skill, supported_skills({"skills": ["Boom", "Mad"]}))

  def test_mad_recipe_keeps_its_token_before_the_size(self):
    die = {"recipe": "(X)&", "description": "X Mad Swing Die",
           "sides": 12, "value": 5, "properties": []}
    self.assertEqual("X&-12:5", bmai.recipe(die))

  def test_parsing_only_skills_are_never_supported(self):
    # BMAIR 0.14.0 parsed Radioactive without decaying it.
    self.assertIn("Radioactive", bmai_supported_skills)
    self.assertNotIn("Radioactive", supported_skills(
        {"skills": ["Fire"], "parsing_only_skills": ["Radioactive"]}))
    self.assertIn("Radioactive", supported_skills(
        {"skills": ["Fire", "Radioactive"], "parsing_only_skills": []}))
    self.assertNotIn("Rush", supported_skills(
        {"skills": ["Rush"], "parsing_only_skills": ["Rush"]}))

  @patch("bmaibagels.game_data.GameData")
  @patch("bmaibagels.monitor.Monitor")
  @patch("bmaibagels.check_output")
  def test_only_binaries_with_time_limit_are_sent_one(self, capabilities, *_):
    def limit_for(settings):
      capabilities.return_value = json.dumps(
          {"engines": [{"name": "montecarlo", "settings": settings}]})
      return BMAIBagels(SimpleNamespace(client=Mock()), ply=2, binary="bmair",
                        time_limit=45).time_limit
    self.assertEqual(45, limit_for(["ply", "time_limit"]))
    self.assertEqual(0, limit_for(["ply"]))

  def test_time_limit_must_be_a_non_negative_number(self):
    for bad in ("-5", "inf", "1e20", "soon"):
      with self.assertRaises(SystemExit), patch("sys.stderr"):
        parse_args(["--time-limit", bad])

  def test_reporting_odds_splits_the_move_time_limit(self):
    self.assertEqual(bmaibagels_module.move_time_limit(60, 0), 60)
    self.assertEqual(bmaibagels_module.move_time_limit(60, 1000), 30)

  @patch("bmaibagels.game_data.GameData")
  @patch("bmaibagels.monitor.Monitor")
  @patch("bmaibagels.check_output")
  def test_rush_challenge_follows_the_bmair_binary(self, capabilities, *_):
    def bagels_for(capability_json):
      capabilities.return_value = capability_json
      bagels = BMAIBagels(SimpleNamespace(client=Mock()), ply=2, binary="bmair")
      bagels.buttons = {
          "Uptown": {"dieTypes": [], "dieSkills": ["Rush"]},
          "Aylee": {"dieTypes": [], "dieSkills": []},
      }
      return bagels

    game = {"gameId": 42, "myButtonName": "Uptown", "opponentButtonName": "Aylee"}
    old = bagels_for('{"commands":["getaction"],"skills":["Fire","Rage"]}')
    self.assertEqual({"Rush"}, old.get_disallowed_skills(game))
    new = bagels_for('{"commands":["getaction"],"skills":["Fire","Rage","Rush"]}')
    self.assertEqual(set(), new.get_disallowed_skills(game))

  def test_special_buttons_need_their_specials_advertised(self):
    summary = {"gameId": 42, "myButtonName": "Largo", "opponentButtonName": "Aylee"}
    self.assertEqual({"Largo"}, BMAIBagels.get_disallowed_buttons(summary))
    self.assertEqual({"Largo"}, BMAIBagels.get_disallowed_buttons(
        summary, frozenset({"skill_immune"})))
    self.assertEqual(set(), BMAIBagels.get_disallowed_buttons(
        summary, frozenset({"no_skill_attacks"})))

  def test_advertised_specials_require_the_special_command(self):
    from bmaibagels import advertised_specials
    listed = {"button_specials": [{"id": "no_initiative"}]}
    self.assertEqual(frozenset(), advertised_specials(listed))
    self.assertEqual(frozenset({"no_initiative"}), advertised_specials(
        {**listed, "commands": ["special"]}))

  def test_dump_names_each_players_button_specials_when_supported(self):
    def player(button, waiting):
      return {
          "button": {"name": button},
          "waitingOnAction": waiting,
          "roundScore": 0,
          "activeDieArray": [{
              "recipe": "(20)", "description": "20-sided die", "sides": 20,
              "value": 5, "properties": [], "skills": [],
          }],
      }
    game = {
        "maxWins": 3,
        "gameState": "START_TURN",
        "player": player("Gordo", True),
        "opponent": player("The Japanese Beetle", False),
    }
    with_specials = bmai.dump(game, ply=1, specials=True)
    self.assertIn("special 0 unique_sizes\nspecial 1 skill_immune\nply 1\n", with_specials)
    self.assertNotIn("special", bmai.dump(game, ply=1))
    self.assertIn("maxbranch 400\ntime_limit 60\n", bmai.dump(game, ply=1, time_limit=60))
    self.assertNotIn("time_limit", bmai.dump(game, ply=1))

  def test_rush_recipe_keeps_the_bmair_rush_token(self):
    self.assertEqual("#10:4", bmai.recipe({
        "recipe": "#(10)",
        "description": "Rush 10-sided die",
        "sides": 10,
        "value": 4,
        "properties": [],
    }))

  def test_rejects_random_button_challenge_before_recipe_generation(self):
    bagels = self.make_bagels()
    bagels.buttons = {
        "RandomBMMixed": {"dieTypes": [], "dieSkills": []},
        "Aylee": {"dieTypes": [], "dieSkills": []},
    }
    bagels.client.wrap_react_to_new_game = Mock()

    bagels.new_challenge({
        "gameId": 42,
        "myButtonName": "RandomBMMixed",
        "opponentButtonName": "Aylee",
    })

    bagels.client.wrap_react_to_new_game.assert_called_once_with(42, "reject")

  def test_new_random_button_challenge_is_rejected_even_for_game_119416(self):
    bagels = self.make_bagels()
    bagels.buttons = {
        "RandomBMMixed": {"dieTypes": [], "dieSkills": []},
        "Aylee": {"dieTypes": [], "dieSkills": []},
    }
    bagels.client.wrap_react_to_new_game = Mock()

    bagels.new_challenge({
        "gameId": 119416,
        "myButtonName": "RandomBMMixed",
        "opponentButtonName": "Aylee",
    })

    bagels.client.wrap_react_to_new_game.assert_called_once_with(
        119416, "reject")

  def test_echo_is_not_explicitly_unsupported_in_python(self):
    game = {
        "myButtonName": "Echo",
        "opponentButtonName": "Aylee",
    }

    self.assertEqual(set(), BMAIBagels.get_disallowed_buttons(game))
    self.assertNotIn("Echo", bmai_unsupported_buttons)

  def test_echo_keeps_its_existing_challenge_behavior(self):
    bagels = self.make_bagels()
    bagels.buttons = {
        "Echo": {"dieTypes": [], "dieSkills": []},
        "Aylee": {"dieTypes": [], "dieSkills": []},
    }
    bagels.client.wrap_react_to_new_game = Mock()

    bagels.new_challenge({
        "gameId": 43,
        "myButtonName": "Echo",
        "opponentButtonName": "Aylee",
    })

    bagels.client.wrap_react_to_new_game.assert_called_once_with(43, "accept")

  def test_attacking_turbos_uses_zero_based_indices_for_list_data(self):
    game = {
        "player": {
            "turboSizeArray": [[1, 20], [1, 20]],
        },
    }

    self.assertEqual(
        {0: [1, 20]},
        BMAIBagels.attacking_turbos(game, "3 0"),
    )

  def test_attacking_turbos_excludes_non_attacking_turbo_dice(self):
    game = {
        "player": {
            "turboSizeArray": {
                "2": [4, 5, 6],
                "1": [4, 5, 6],
            },
        },
    }

    self.assertEqual(
        {"1": [4, 5, 6]},
        BMAIBagels.attacking_turbos(game, "1"),
    )

  def test_attacking_turbos_follow_active_die_order(self):
    game = {
        "player": {
            "turboSizeArray": {
                "3": [4, 5, 6],
                "1": [4, 5, 6],
            },
        },
    }

    turbos = BMAIBagels.attacking_turbos(game, "3 1")

    self.assertEqual(["1", "3"], list(turbos))

  def test_submit_auxiliary_accepts_selected_die(self):
    bagels = self.make_bagels()

    self.assertTrue(bagels.submit_auxiliary({"gameId": 42}, "aux 5"))
    bagels.client.client.choose_auxiliary_dice.assert_called_once_with(
        42, "add", "5")

  def test_submit_auxiliary_declines_without_a_die(self):
    bagels = self.make_bagels()

    self.assertTrue(bagels.submit_auxiliary({"gameId": 42}, "aux -1"))
    bagels.client.client.choose_auxiliary_dice.assert_called_once_with(
        42, "decline")

  def test_bad_game_writes_debug_files_beside_the_script(self):
    bagels = self.make_bagels()
    bagels.bad_games = []
    with TemporaryDirectory() as directory, patch.object(
        bmaibagels_module, "GAME_DEBUG_DIR", Path(directory) / "game-debug"):
      bagels.bad_game(42, "game input", "diagnostic output")

      self.assertEqual(
          "game input",
          (Path(directory) / "game-debug/42-input.txt").read_text())
      self.assertEqual(
          "diagnostic output",
          (Path(directory) / "game-debug/42-output.txt").read_text())
      self.assertEqual([42], bagels.bad_games)

  def test_network_error_loading_game_does_not_mark_it_bad(self):
    bagels = self.make_bagels()
    bagels.bad_games = []
    bagels.game_data = SimpleNamespace(
        fetch=Mock(side_effect=BMNetworkError("offline")))
    bagels.bad_game = Mock()

    self.assertFalse(bagels.monitor_handler({"gameId": 42}))
    self.assertEqual([], bagels.bad_games)
    bagels.bad_game.assert_not_called()

  def test_network_error_submitting_move_does_not_mark_it_bad(self):
    bagels = self.make_bagels()
    bagels.bad_games = []
    bagels.ply = 1
    bagels.game_data = SimpleNamespace(fetch=Mock(return_value={
        "gameId": 42,
        "gameState": "START_TURN",
        "maxWins": 3,
        "player": {
            "playerName": "BMAIBagels",
            "button": {"name": "Aylee"},
            "activeDieArray": [],
            "roundScore": 0,
            "waitingOnAction": True,
        },
        "opponent": {
            "playerName": "Opponent",
            "button": {"name": "Avis"},
            "activeDieArray": [],
            "roundScore": 0,
        },
    }))
    bagels.get_disallowed_skills = Mock(return_value=set())
    bagels.get_disallowed_buttons = Mock(return_value=set())
    bagels.exec_bmai = Mock(side_effect=BMNetworkError("connection reset"))
    bagels.bad_game = Mock()

    self.assertFalse(bagels.monitor_handler({"gameId": 42}))
    self.assertEqual([], bagels.bad_games)
    bagels.bad_game.assert_not_called()

  def test_timeouts_fall_back_to_ply_one_and_never_ply_zero(self):
    bagels = self.make_bagels()
    bagels.bad_games = []
    bagels.ply = 3
    bagels.game_data = SimpleNamespace(fetch=Mock(return_value={
        "gameId": 42,
        "gameState": "START_TURN",
        "maxWins": 3,
        "player": {
            "playerName": "BMAIBagels",
            "button": {"name": "Aylee"},
            "activeDieArray": [],
            "roundScore": 0,
            "waitingOnAction": True,
        },
        "opponent": {
            "playerName": "Opponent",
            "button": {"name": "Avis"},
            "activeDieArray": [],
            "roundScore": 0,
        },
    }))
    bagels.get_disallowed_skills = Mock(return_value=set())
    bagels.get_disallowed_buttons = Mock(return_value=set())
    bagels.exec_bmai = Mock(side_effect=FunctionTimedOut())
    bagels.bad_game = Mock()
    plies = []
    real_dump = bmai.dump

    def dump(game, ply=3, **kwargs):
      plies.append(ply)
      return real_dump(game, ply=ply, **kwargs)

    with patch("bmaibagels.game_data.bmai.dump", side_effect=dump):
      self.assertFalse(bagels.monitor_handler({"gameId": 42}))
    self.assertEqual([3, 2, 1], plies)
    bagels.bad_game.assert_called_once()

  def test_pending_fire_attack_is_cancelled_and_recalculated(self):
    bagels = self.make_bagels()
    bagels.bad_games = []
    bagels.client.client.adjust_fire_dice.return_value = SimpleNamespace(
        status="ok", message="cancelled")
    pending = {
        "gameId": 42,
        "gameState": "ADJUST_FIRE_DICE",
        "roundNumber": 2,
        "timestamp": 100,
    }
    ended = {"gameId": 42, "gameState": "END_GAME"}
    bagels.game_data = SimpleNamespace(fetch=Mock(side_effect=[pending, ended]))

    self.assertTrue(bagels.monitor_handler({"gameId": 42}))
    bagels.client.client.adjust_fire_dice.assert_called_once_with(
        42,
        "cancel",
        [],
        [],
        roundNumber=2,
        timestamp=100,
    )


class TestBMAPIRequestRetries(unittest.TestCase):

  @staticmethod
  def make_client(post):
    client = BMClient.__new__(BMClient)
    client.url = "https://example.invalid/api"
    client.session = SimpleNamespace(post=post)
    return client

  def test_read_request_retries_transient_network_errors(self):
    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {
        "status": "ok", "message": "loaded", "data": {}}
    post = Mock(side_effect=[
        requests.exceptions.ConnectionError("first"),
        requests.exceptions.Timeout("second"),
        response,
    ])
    client = self.make_client(post)

    with patch("bmapi.time.sleep"):
      result = client._make_request({"type": "loadGameData", "game": 42})

    self.assertEqual("ok", result.status)
    self.assertEqual(3, post.call_count)

  def test_write_request_is_not_retried_when_result_is_uncertain(self):
    post = Mock(side_effect=requests.exceptions.ConnectionError("offline"))
    client = self.make_client(post)

    with self.assertRaises(BMNetworkError):
      client._make_request({"type": "submitTurn", "game": 42})

    self.assertEqual(1, post.call_count)

class TestBMAIInput(unittest.TestCase):

  def test_explicitly_unsupported_button_set(self):
    self.assertEqual(set(), bmai_unsupported_buttons)

  def test_python_support_list_matches_bmair_capability_contract(self):
    bmair_capabilities = {
        "Auxiliary", "Berserk", "Chance", "Doppelganger", "Fire", "Focus",
        "Insult", "Jolt", "Konstant", "Maximum", "Mighty", "Mood",
        "Morphing", "Null", "Option", "Ornery", "Poison", "Queer",
        "Radioactive", "Rage", "Reserve", "Shadow", "Slow", "Speed",
        "Stealth", "Stinger", "TimeAndSpace", "Trip", "Turbo", "Twin",
        "Unique", "Unskilled", "Value", "Warrior", "Weak",
    }
    # BMAIR advertises one generic Swing capability. ButtonWeavers reports
    # the individual swing letters as distinct die types.
    bmair_capabilities.update(
        f"{letter} Swing" for letter in "PQRSTUVWXYZ")

    self.assertEqual(bmair_capabilities, bmai_supported_skills)

  def test_every_evaluation_uses_native_mode_with_automatic_workers(self):
    player = {
        "activeDieArray": [],
        "roundScore": 0,
        "waitingOnAction": True,
    }
    opponent = {
        "activeDieArray": [],
        "roundScore": 0,
        "waitingOnAction": False,
    }
    game = {
        "maxWins": 3,
        "gameState": "START_TURN",
        "player": player,
        "opponent": opponent,
    }

    generated_input = bmai.dump(game)

    self.assertTrue(generated_input.startswith(
        "mode native\nworkers auto\nfire_overshooting on\ngame 3\n"))

  def test_every_evaluation_enables_fire_overshooting(self):
    game = {
        "maxWins": 3,
        "gameState": "START_TURN",
        "player": {
            "activeDieArray": [],
            "roundScore": 0,
            "waitingOnAction": True,
        },
        "opponent": {
            "activeDieArray": [],
            "roundScore": 0,
            "waitingOnAction": False,
        },
    }

    self.assertIn("\nfire_overshooting on\n", bmai.dump(game))

  def test_selected_move_report_is_opt_in(self):
    game = {
        "maxWins": 3,
        "gameState": "START_TURN",
        "player": {
            "activeDieArray": [],
            "roundScore": 0,
            "waitingOnAction": True,
        },
        "opponent": {
            "activeDieArray": [],
            "roundScore": 0,
            "waitingOnAction": False,
        },
    }

    self.assertNotIn("report_sims", bmai.dump(game))
    self.assertIn("\nreport_sims 1000\n", bmai.dump(game, report_sims=1000))


if __name__ == "__main__":
  unittest.main()
