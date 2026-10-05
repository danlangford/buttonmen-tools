import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock

import bmutils

from watchadventure import (
    Acceptance,
    AdventureMonitor,
    ButtonEligibility,
    ButtonFilterPublication,
    FightLog,
    GameHistoryAdventureMonitor,
    IneligibleGame,
    LeaderboardEntry,
    QuestLog,
    completed_game_adventure_can_retire,
    duplicate_rejection_reason,
    eligibility_rejection_reason,
    game_description_matches,
    leaderboard_content_equal,
    load_config,
    mission_game_description,
    parse_acceptance,
    parse_quest_logs,
    render_leaderboard,
    render_quest,
)


PROJECT = Path(__file__).resolve().parent
LOOKING_GLASS = PROJECT / "adventures/operation-looking-glass.toml"
EXAMPLE = PROJECT / "adventures/example-adventure.toml"
OZ = PROJECT / "adventures/operation-oz.toml"
DRAGONSTORM = PROJECT / "adventures/operation-dragonstorm.toml"
YETI_QUEST = PROJECT / "adventures/operation-yeti-quest.toml"
PETTING_ZOO = PROJECT / "adventures/operation-petting-zoo.toml"


class TestAdventureConfig(unittest.TestCase):

  def test_new_operations_have_distinct_entry_points_and_leaderboards(self):
    configs = [load_config(path) for path in (
        DRAGONSTORM, YETI_QUEST, PETTING_ZOO)]

    self.assertEqual(
        [(1374, 34585, "Trogdor", 5),
         (1375, 34586, "Lark", 6),
         (1376, 34587, "Cheese Weasel", 7)],
        [(config.thread_id, config.leaderboard_post_id,
          config.fights[0].opponent_button, config.fight_count)
         for config in configs])
    self.assertEqual(3, len({config.slug for config in configs}))
    self.assertTrue(all(
        config.entry_mode == "completed_game" for config in configs))

  def test_looking_glass_config_describes_current_adventure(self):
    config = load_config(LOOKING_GLASS)

    self.assertEqual(1368, config.thread_id)
    self.assertEqual("player", config.button_uniqueness)
    self.assertIsNone(config.minimum_win_rate)
    self.assertEqual(60.0, config.maximum_win_rate)
    self.assertEqual(
        "operation_looking_glass_oz_button_name",
        config.win_rate_allowlist)
    self.assertEqual(6, config.fight_count)
    self.assertEqual("Tweedledum+dee", config.fights[0].opponent_button)
    self.assertEqual("Alice", config.fights[-1].opponent_button)
    self.assertEqual(
        ["👯", "🎩", "🐇", "♥️", "🐉", "👧"],
        [fight.emoji for fight in config.fights])
    self.assertTrue(config.import_looking_glass_v2)

  def test_example_config_is_a_complete_independent_adventure(self):
    config = load_config(EXAMPLE)

    self.assertEqual("clockwork-gauntlet", config.slug)
    self.assertEqual("global", config.button_uniqueness)
    self.assertEqual((45.0, 55.0),
                     (config.minimum_win_rate, config.maximum_win_rate))
    self.assertEqual(3, config.fight_count)
    self.assertFalse(config.leaderboard_enabled)
    self.assertFalse(config.import_looking_glass_v2)

  def test_oz_uses_completed_games_beginning_september_tenth_utc(self):
    config = load_config(OZ)

    self.assertEqual("completed_game", config.entry_mode)
    self.assertEqual(1788998400, config.games_created_after)
    self.assertIsNone(config.submissions_close_at)
    self.assertEqual(3, config.entry_target_wins)
    self.assertEqual(34259, config.leaderboard_post_id)
    self.assertEqual(34258, config.announcement_post_id)
    self.assertEqual(
        ["Pythagoras", "Smith", "Lion (SU)", "Monkeys", "Chrysalis",
         "Magician", "Totoro"],
        [fight.opponent_button for fight in config.fights])
    self.assertEqual(
        ["🌾", "🪓", "🦁", "🐒", "🧙‍♀️", "🎭", "🐕"],
        [fight.emoji for fight in config.fights])
    self.assertEqual(
        "Toto has escaped! Can you find your little dog and still get home "
        "somehow?", config.fights[-1].description)

  def test_completed_game_config_parses_submission_cutoff(self):
    source = OZ.read_text(encoding="utf-8").replace(
        'games_created_after = "2026-09-10"',
        'games_created_after = "2026-09-10"\n'
        'submissions_close_at = "2026-10-15T12:30:00Z"')
    with TemporaryDirectory() as directory:
      path = Path(directory) / "closing.toml"
      path.write_text(source, encoding="utf-8")

      config = load_config(path)

    self.assertEqual(1792067400, config.submissions_close_at)


class TestAdventureRecords(unittest.TestCase):

  def setUp(self):
    self.config = load_config(EXAMPLE)

  def test_acceptance_command_must_start_the_post(self):
    accepted = {
        "postId": 10, "posterName": "Alice",
        "body": "I accept [button=Aylee]", "deleted": False}
    chat = {
        **accepted, "postId": 11,
        "body": "Talking about things\nI accept [button=Aylee]"}

    self.assertEqual(
        Acceptance(10, "Alice", "Aylee"),
        parse_acceptance(accepted, self.config))
    self.assertIsNone(parse_acceptance(chat, self.config))

  def test_generic_record_round_trips_without_parsing_display_text(self):
    quest = QuestLog(
        0, 10, "Alice", "Aylee", "active",
        (FightLog(1, 123, "the Gatekeeper", "active"),))
    body = render_quest(quest, self.config)
    body = body.replace("[GAUNTLET: Alice - Aylee]", "Any human heading")
    post = {
        "postId": 20, "posterName": "BMAIBagels",
        "body": body, "deleted": False}

    self.assertEqual(
        replace(quest, forum_post_id=20),
        parse_quest_logs([post], self.config)[0])

  def test_looking_glass_adapter_uses_mission_header_identity(self):
    config = load_config(LOOKING_GLASS)
    post = {
        "postId": 30,
        "posterName": "BMAIBagels",
        "deleted": False,
        "body": (
            "[MISSION: Current Player - Current Button]\n"
            "[code]OPERATION LOOKING GLASS | source-post=29 | "
            "player=Old Player | button=Old Button | status=active\n"
            "LOOKING GLASS FIGHT | number=1 | game=119245 | "
            "opponent=Tweedledum+dee | result=active[/code]"),
    }

    quest = parse_quest_logs([post], config)[0]

    self.assertEqual(("Current Player", "Current Button"),
                     (quest.player, quest.button))
    self.assertEqual(119245, quest.current_fight.game_id)


class TestAdventureRules(unittest.TestCase):

  def setUp(self):
    self.config = load_config(EXAMPLE)
    self.logs = [QuestLog(
        20, 10, "Alice", "Aylee", "active",
        (FightLog(1, 123, "the Gatekeeper", "active"),))]

  def test_global_player_and_none_uniqueness_are_distinct(self):
    bob = Acceptance(11, "Bob", "Aylee")
    alice = Acceptance(12, "alice", "aylee")

    self.assertIsNotNone(
        duplicate_rejection_reason(bob, self.logs, self.config))
    player_scope = replace(self.config, button_uniqueness="player")
    self.assertIsNone(
        duplicate_rejection_reason(bob, self.logs, player_scope))
    self.assertIsNotNone(
        duplicate_rejection_reason(alice, self.logs, player_scope))
    no_scope = replace(self.config, button_uniqueness="none")
    self.assertIsNone(
        duplicate_rejection_reason(alice, self.logs, no_scope))

  def test_win_rate_boundaries_are_minimum_inclusive_maximum_exclusive(self):
    self.assertIsNone(eligibility_rejection_reason(
        ButtonEligibility("Low edge", True, 45.0), self.config))
    self.assertIsNone(eligibility_rejection_reason(
        ButtonEligibility("Inside", True, 54.99), self.config))
    self.assertIn("at least 45%", eligibility_rejection_reason(
        ButtonEligibility("Too low", True, 44.99), self.config))
    self.assertIn("less than 55%", eligibility_rejection_reason(
        ButtonEligibility("High edge", True, 55.0), self.config))

  def test_allowlist_bypasses_only_win_rate_rules(self):
    config = load_config(OZ)
    button_filter = Mock()
    button_filter.button_in_allowlist.return_value = True

    self.assertIsNone(eligibility_rejection_reason(
        ButtonEligibility("Brenda", True, 61.88), config, button_filter))
    self.assertIn("cannot read", eligibility_rejection_reason(
        ButtonEligibility("Unsupported", False, 61.88),
        config, button_filter))

  def test_published_operation_allowlist_contains_old_and_new_qualifiers(self):
    publication = ButtonFilterPublication(
        PROJECT.parent)
    allowlist = "operation_looking_glass_oz_button_name"

    self.assertTrue(publication.button_in_allowlist("Brenda", allowlist))
    self.assertTrue(publication.button_in_allowlist("KaijuGamer", allowlist))


class TestCompleteAdventureProgression(unittest.TestCase):

  @staticmethod
  def completed_game(player_won=True):
    return {
        "gameState": "END_GAME",
        "playerDataArray": [
            {"playerName": "AlicePlayer",
             "gameScoreArray": {"W": 3 if player_won else 1}},
            {"playerName": "BMAIBagels",
             "gameScoreArray": {"W": 1 if player_won else 3}},
        ],
    }

  def test_example_config_can_drive_an_entire_adventure(self):
    config = load_config(EXAMPLE)
    client = Mock()
    client.wrap_create_game.side_effect = [
        {"gameId": 101}, {"gameId": 102}, {"gameId": 103}]
    client.wrap_load_game_data.side_effect = [
        self.completed_game(), self.completed_game(), self.completed_game()]
    monitor = AdventureMonitor(client, Mock(), config)
    quest = QuestLog(50, 40, "AlicePlayer", "Aylee", "creating", ())

    quest = monitor.reconcile_quest(quest, [])
    self.assertEqual((1, 101, "active"), (
        quest.current_fight.number, quest.current_fight.game_id,
        quest.current_fight.result))
    quest = monitor.reconcile_quest(quest, [])
    self.assertEqual((2, 102),
                     (quest.current_fight.number, quest.current_fight.game_id))
    quest = monitor.reconcile_quest(quest, [])
    self.assertEqual((3, 103),
                     (quest.current_fight.number, quest.current_fight.game_id))
    quest = monitor.reconcile_quest(quest, [])

    self.assertEqual("survived", quest.status)
    self.assertEqual(["won", "won", "won"],
                     [fight.result for fight in quest.fights])
    self.assertEqual(
        ["CLOCKWORK GAUNTLET TRIAL 1/3 | MISSION 40 | "
         "Cross the first threshold.",
         "CLOCKWORK GAUNTLET TRIAL 2/3 | MISSION 40 | "
         "Pass the knight who guards the inner mechanism.",
         "CLOCKWORK GAUNTLET TRIAL 3/3 | MISSION 40 | "
         "Face the guardian at the heart of the machine."],
        [call.args[-1] for call in client.wrap_create_game.call_args_list])

  def test_none_uniqueness_adopts_only_the_same_mission_game(self):
    config = replace(load_config(EXAMPLE), button_uniqueness="none")
    client = Mock()
    client.wrap_load_game_data.side_effect = lambda game_id: {
        "description": {
            201: "CLOCKWORK GAUNTLET TRIAL 1/3 | MISSION 39 | "
                 "Cross the first threshold.",
            202: "CLOCKWORK GAUNTLET TRIAL 1/3 | MISSION 40 | "
                 "Cross the first threshold.",
        }[game_id]
    }
    monitor = AdventureMonitor(client, Mock(), config)
    quest = QuestLog(50, 40, "AlicePlayer", "Aylee", "creating", ())
    available = [
        {"gameId": game_id, "opponentName": "AlicePlayer",
         "myButtonName": "Avis", "opponentButtonName": "Aylee"}
        for game_id in (201, 202)
    ]

    fight = monitor.create_fight(quest, 1, available)

    self.assertEqual(202, fight.game_id)
    self.assertEqual(
        "CLOCKWORK GAUNTLET TRIAL 1/3 | MISSION 40 | "
        "Cross the first threshold.",
        mission_game_description(config, 1, 40))
    client.wrap_create_game.assert_not_called()


class TestLeaderboard(unittest.TestCase):

  @staticmethod
  def entry(player, button, defeated, rounds_lost=1, points=100):
    return LeaderboardEntry(
        player, button, defeated, rounds_lost, points, "active", None)

  def test_opponent_sections_preserve_global_ranks(self):
    config = load_config(OZ)
    entries = [
        LeaderboardEntry("Alice", "Aylee", 2, 1, 100, "active", None),
        LeaderboardEntry("Bob", "Avis", 2, 2, 90, "active", None),
        LeaderboardEntry("Carol", "Echo", 1, 0, 80, "active", None),
    ]

    body = render_leaderboard(entries, config)
    self.assertIn("[b]LION (SU)[/b]\n1. Alice", body)
    self.assertIn("2. Bob", body)
    self.assertIn("\n\n[b]SMITH[/b]\n3. Carol", body)
    self.assertEqual(3, body.count(" — ⏳"))

  def test_terminal_outcomes_use_section_headers_and_compact_markers(self):
    config = load_config(LOOKING_GLASS)
    entries = [
        LeaderboardEntry("Alice", "Aylee", 6, 1, 100,
                         "survived", None),
        LeaderboardEntry("Bob", "Avis", 5, 2, 90,
                         "failed", "Alice"),
        LeaderboardEntry("Carol", "Echo", 4, 3, 80,
                         "failed", "The Jabberwock"),
    ]

    body = render_leaderboard(entries, config)

    self.assertIn("[b]SURVIVED[/b]\n1. Alice — Aylee", body)
    self.assertIn(" — 🏆", body)
    self.assertIn("[b]ALICE[/b]\n2. Bob — Avis", body)
    self.assertIn("[b]THE JABBERWOCK[/b]\n3. Carol — Echo", body)
    self.assertIn("2. Bob — Avis — 5F/2L/90pts — 👧", body)
    self.assertIn("3. Carol — Echo — 4F/3L/80pts — 🐉", body)
    self.assertNotIn(" — ❌", body)
    self.assertNotIn("DEFEATED by", body)

  def test_sections_do_not_reveal_unreached_opponents(self):
    config = load_config(OZ)
    entries = [
        LeaderboardEntry("Alice", "Aylee", 1, 0, 42,
                         "failed", "Smith"),
    ]

    body = render_leaderboard(entries, config)

    self.assertIn("[b]SMITH[/b]", body)
    for hidden in ("LION (SU)", "MONKEYS", "CHRYSALIS", "MAGICIAN", "TOTORO"):
      self.assertNotIn(f"[b]{hidden}[/b]", body)

  def test_leaderboard_is_rewritten_only_when_rendered_body_changes(self):
    config = replace(
        load_config(EXAMPLE), leaderboard_enabled=True,
        leaderboard_post_id=77)
    client = Mock()
    monitor = AdventureMonitor(client, Mock(), config)
    post = {"postId": 77, "body": "old leaderboard"}

    monitor.update_leaderboard([], [post])
    replacement = client.wrap_edit_forum_post.call_args.args[1]
    self.assertIn("ADVENTURE_LEADERBOARD_V4 clockwork-gauntlet", replacement)

    client.reset_mock()
    monitor.update_leaderboard([], [{"postId": 77, "body": replacement}])
    client.wrap_edit_forum_post.assert_not_called()

  def test_only_new_or_changed_rows_are_bold(self):
    config = load_config(OZ)
    old = self.entry("Alice", "Aylee", 2)
    added = self.entry("Bob", "Avis", 1)
    previous = render_leaderboard([old], config)

    body = render_leaderboard([old, added], config, previous_body=previous)

    self.assertIn("1. Alice — Aylee", body)
    self.assertNotIn("[b]1. Alice — Aylee", body)
    self.assertIn("[b]2. Bob — Avis", body)

    advanced = self.entry("Alice", "Aylee", 3)
    changed = render_leaderboard(
        [advanced, added], config, previous_body=body)
    self.assertIn("[b]1. Alice — Aylee", changed)
    self.assertNotIn("[b]2. Bob — Avis", changed)

  def test_polling_and_restart_preserve_existing_highlight_without_edit(self):
    config = load_config(OZ)
    old = self.entry("Alice", "Aylee", 2)
    added = self.entry("Bob", "Avis", 1)
    previous = render_leaderboard([old], config)
    highlighted = render_leaderboard(
        [old, added], config, previous_body=previous)

    rerendered = render_leaderboard(
        [old, added], config, previous_body=highlighted)

    self.assertNotEqual(highlighted, rerendered)
    self.assertTrue(leaderboard_content_equal(highlighted, rerendered))
    self.assertIn("[b]2. Bob — Avis", highlighted)

  def test_first_managed_rewrite_does_not_bold_every_row(self):
    config = load_config(OZ)
    body = render_leaderboard(
        [self.entry("Alice", "Aylee", 1)], config,
        previous_body="reserved for leaderboard")

    self.assertNotIn("[b]1. Alice — Aylee", body)

  def test_highlighting_uses_each_adventures_custom_entry_template(self):
    config = load_config(EXAMPLE)
    old = self.entry("Alice", "Aylee", 2)
    added = self.entry("Bob", "Avis", 1)
    previous = render_leaderboard([old], config)

    body = render_leaderboard([old, added], config, previous_body=previous)

    self.assertNotIn("[b]1. Alice — Aylee", body)
    self.assertIn("[b]2. Bob — Avis", body)

  def test_template_migration_does_not_bold_every_existing_row(self):
    config = load_config(OZ)
    entry = self.entry("Alice", "Aylee", 1)
    previous = render_leaderboard([entry], config)
    compact = replace(
        config, templates=replace(
            config.templates,
            leaderboard_entry=(
                "{rank}. {player} — {button} — "
                "{opponents_defeated}F/{rounds_lost}L — {outcome}")))

    body = render_leaderboard([entry], compact, previous_body=previous)

    self.assertNotIn("[b]1. Alice — Aylee", body)

  def test_template_migration_with_ineligible_rows_does_not_bold_entries(self):
    config = load_config(OZ)
    entry = self.entry("Alice", "Aylee", 1)
    rejected = IneligibleGame(123, "Bob", "Avis", "wrong target wins")
    previous = render_leaderboard([entry], config, [rejected])
    compact = replace(
        config, templates=replace(
            config.templates,
            leaderboard_entry=(
                "{rank}. {player} — {button} — "
                "{opponents_defeated}F/{rounds_lost}L — {outcome}")))

    body = render_leaderboard(
        [entry], compact, [rejected], previous_body=previous)

    self.assertNotIn("[b]1. Alice — Aylee", body)

  def test_old_marker_forces_one_clean_rewrite_after_bad_migration(self):
    config = load_config(OZ)
    entry = self.entry("Alice", "Aylee", 1)
    current = render_leaderboard([entry], config)
    damaged = current.replace(
        "1. Alice — Aylee", "[b]1. Alice — Aylee").replace(
            " — ⏳", " — ⏳[/b]").replace(
                "ADVENTURE_LEADERBOARD_V4", "ADVENTURE_LEADERBOARD_V3")

    repaired = render_leaderboard([entry], config, previous_body=damaged)

    self.assertNotIn("[b]1. Alice — Aylee", repaired)
    self.assertIn("ADVENTURE_LEADERBOARD_V4 operation-oz", repaired)
    self.assertFalse(leaderboard_content_equal(damaged, repaired))


class TestCompletedGameEntry(unittest.TestCase):

  @staticmethod
  def game(game_id, player="Alice", button="Aylee", target=3,
           player_wins=3, bot_button="Pythagoras"):
    return {
        "gameId": game_id,
        "playerNameA": player,
        "buttonNameA": button,
        "roundsWonA": player_wins,
        "playerNameB": "BMAIBagels",
        "buttonNameB": bot_button,
        "roundsWonB": target if player_wins < target else 1,
        "targetWins": target,
    }

  def setUp(self):
    self.config = load_config(OZ)
    self.client = Mock()
    self.button_filter = Mock()
    self.button_filter.button_in_allowlist.return_value = False
    self.button_filter.eligibility.return_value = ButtonEligibility(
        "Aylee", True, 50.0)
    self.monitor = GameHistoryAdventureMonitor(
        self.client, self.button_filter, self.config)

  def test_first_player_button_game_is_authoritative_even_if_misconfigured(self):
    accepted, ineligible = self.monitor.entry_games([
        self.game(100, target=1, player_wins=1),
        self.game(101),
    ])

    self.assertEqual([], accepted)
    self.assertEqual([100, 101], [game.game_id for game in ineligible])
    self.assertIn("first to 3", ineligible[0].reason)
    self.assertIn("[game=100] is authoritative", ineligible[1].reason)

  def test_submission_cutoff_keeps_old_entries_and_rejects_new_ones(self):
    close_at = 1800000000
    self.monitor.config = replace(
        self.config, submissions_close_at=close_at)

    accepted, ineligible = self.monitor.entry_games([
        {**self.game(100), "gameStart": close_at - 1},
        {**self.game(101, player="Bob"), "gameStart": close_at},
    ])

    self.assertEqual([100], [int(game[0]["gameId"]) for game in accepted])
    self.assertEqual([101], [game.game_id for game in ineligible])
    self.assertIn("submissions were closed", ineligible[0].reason)

  def test_closed_adventure_retires_only_after_all_visible_work_finishes(self):
    close_at = 1800000000
    config = replace(self.config, submissions_close_at=close_at)
    active = QuestLog(
        0, 100, "Alice", "Aylee", "active",
        (FightLog(1, 100, "Pythagoras", "active"),))
    opening_game = {
        "myButtonName": "Pythagoras", "nTargetWins": 3}

    self.assertFalse(completed_game_adventure_can_retire(
        config, [], [], now=close_at - 1))
    self.assertFalse(completed_game_adventure_can_retire(
        config, [active], [], now=close_at))
    self.assertFalse(completed_game_adventure_can_retire(
        config, [], [opening_game], now=close_at))
    self.assertTrue(completed_game_adventure_can_retire(
        config, [], [], now=close_at))

  def test_history_is_paginated_for_both_player_positions_and_deduplicated(self):
    self.monitor.HISTORY_PAGE_SIZE = 2
    self.client.wrap_search_game_history.side_effect = [
        {"games": [self.game(100), self.game(101)]},
        {"games": []},
        {"games": [self.game(101), self.game(102)]},
        {"games": []},
    ]

    history = self.monitor.load_history()

    self.assertEqual([100, 101, 102],
                     [int(game["gameId"]) for game in history])
    first_query = self.client.wrap_search_game_history.call_args_list[0].kwargs
    self.assertEqual(1788998400, first_query["gameStartMin"])
    self.assertEqual("BMAIBagels", first_query["playerNameA"])
    third_query = self.client.wrap_search_game_history.call_args_list[2].kwargs
    self.assertEqual("BMAIBagels", third_query["playerNameB"])

  def test_pythagoras_win_creates_smith_game_linking_to_entry(self):
    gate = self.game(100)
    self.client.wrap_create_game.return_value = {"gameId": 200}

    quest = self.monitor.rebuild_quest(
        gate, "Alice", "Aylee", [gate], [])

    self.assertEqual("active", quest.status)
    self.assertEqual(["won", "active"], [fight.result for fight in quest.fights])
    self.assertEqual("Smith", quest.current_fight.opponent)
    self.client.wrap_create_game.assert_called_once_with(
        "Smith", "Aylee", "BMAIBagels", "Alice",
        "OPERATION OZ FIGHT 2/7 | ENTRY GAME 100 | Free the Tin Man and "
        "navigate the dark forest full of lions and tigers and bears (oh my!).",
        max_wins=3, previous_game_id=100)

  def test_pythagoras_loss_is_a_terminal_mission_without_another_game(self):
    gate = self.game(100, player_wins=1)

    quest = self.monitor.rebuild_quest(
        gate, "Alice", "Aylee", [gate], [])

    self.assertEqual("failed", quest.status)
    self.assertEqual("lost", quest.current_fight.result)
    self.client.wrap_create_game.assert_not_called()

  def test_unreadable_and_over_limit_buttons_are_listed_not_accepted(self):
    self.button_filter.eligibility.side_effect = {
        "Fireball": ButtonEligibility("Fireball", False, 50.0),
        "Goliath": ButtonEligibility("Goliath", True, 60.0),
    }.get

    accepted, ineligible = self.monitor.entry_games([
        self.game(100, button="Fireball"),
        self.game(101, player="Bob", button="Goliath"),
    ])

    self.assertEqual([], accepted)
    self.assertIn("cannot read", ineligible[0].reason)
    self.assertIn("less than 60%", ineligible[1].reason)

  def test_completed_chain_is_rebuilt_from_descriptions_and_previous_games(self):
    gate = self.game(100)
    smith = self.game(200, bot_button="Smith")
    lion = self.game(
        300, bot_button="Lion (SU)", player_wins=1)
    self.client.wrap_load_game_data.side_effect = {
        200: {
            "description": "OPERATION OZ FIGHT 2/7 | ENTRY GAME 100 | "
                           "Free the Tin Man and navigate the dark forest full "
                           "of lions and tigers and bears (oh my!).",
            "previousGameId": 100,
        },
        300: {
            "description": "OPERATION OZ FIGHT 3/7 | ENTRY GAME 100 | "
                           "Pacify the Lion and do not succumb to the poppies.",
            "previousGameId": 200,
        },
    }.get

    quest = self.monitor.rebuild_quest(
        gate, "Alice", "Aylee", [gate, smith, lion], [])

    self.assertEqual("failed", quest.status)
    self.assertEqual([100, 200, 300], [fight.game_id for fight in quest.fights])
    self.assertEqual(["won", "won", "lost"],
                     [fight.result for fight in quest.fights])
    self.client.wrap_create_game.assert_not_called()

  def test_fight_that_ends_during_a_check_is_not_created_again(self):
    gate = self.game(100)
    smith = self.game(200, bot_button="Smith")
    smith_live = {
        "gameId": 200, "opponentName": "Alice", "myButtonName": "Smith",
        "opponentButtonName": "Aylee", "nTargetWins": 3}
    # Smith ends right after whichever source is read first; each source
    # keeps the view it had at its first read.
    finished = {"smith": False}
    seen = {}

    def source(name, live, complete):
      def read(*_args, **_kwargs):
        seen.setdefault(name, finished["smith"])
        finished["smith"] = True
        return complete() if seen[name] else live()
      return read

    self.client.wrap_load_active_games.side_effect = source(
        "active", lambda: [dict(smith_live)], lambda: [])
    self.client.wrap_load_new_games.return_value = []
    self.client.wrap_search_game_history.side_effect = source(
        "history", lambda: {"games": [gate]}, lambda: {"games": [gate, smith]})
    self.client.wrap_load_game_data.return_value = {
        "description": "OPERATION OZ FIGHT 2/7 | ENTRY GAME 100",
        "previousGameId": 100}
    self.client.wrap_load_forum_thread.return_value = {"posts": []}
    self.monitor.update_history_leaderboard = Mock()

    self.client.wrap_create_game.return_value = {"gameId": 300}

    self.monitor._check_once_locked()

    created = [call.args[0] for call in self.client.wrap_create_game.call_args_list]
    self.assertEqual(["Lion (SU)"], created)
    quest = self.monitor.update_history_leaderboard.call_args.args[0][0]
    self.assertEqual([100, 200, 300], [fight.game_id for fight in quest.fights])
    self.assertEqual(["won", "won", "active"],
                     [fight.result for fight in quest.fights])

  def test_restart_recognizes_old_description_without_new_flavor(self):
    self.assertTrue(game_description_matches(
        self.config, 2, 100,
        "OPERATION OZ FIGHT 2/7 | ENTRY GAME 100"))
    self.assertTrue(game_description_matches(
        self.config, 2, 100,
        "OPERATION OZ FIGHT 2/7 | ENTRY GAME 100 | Earlier wording"))
    self.assertFalse(game_description_matches(
        self.config, 2, 101,
        "OPERATION OZ FIGHT 2/7 | ENTRY GAME 100 | Earlier wording"))

  def test_leaderboard_shows_active_mission_and_ineligible_game(self):
    entry = LeaderboardEntry(
        "Alice", "Aylee", 1, 0, 42.0, "active", None)
    rejected = IneligibleGame(99, "Bob", "Hammer", "entry games must be first to 3")

    body = render_leaderboard([entry], self.config, [rejected])

    self.assertIn("[b]SMITH[/b]", body)
    self.assertIn(" — ⏳", body)
    self.assertNotIn("MISSION IN PROGRESS", body)
    self.assertIn("[game=99] Bob — Hammer", body)
    self.assertNotIn("ADVENTURE_RECORD", body)


class TestPreviousGameLink(unittest.TestCase):

  def test_wrapper_passes_match_length_and_previous_game_to_api(self):
    parser = object.__new__(bmutils.BMClientParser)
    parser.client = Mock()
    parser.client.create_game.return_value = SimpleNamespace(
        status="ok", data={"gameId": 200})

    result = parser.wrap_create_game(
        "Smith", "Aylee", "BMAIBagels", "Alice", "OPERATION OZ",
        max_wins=3, previous_game_id=100)

    self.assertEqual({"gameId": 200}, result)
    parser.client.create_game.assert_called_once_with(
        "Smith", "Aylee", "BMAIBagels", "Alice", "OPERATION OZ",
        max_wins=3, use_prev_game=100)


if __name__ == "__main__":
  unittest.main()
