import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import watchwonderland
from watchwonderland import (
    Acceptance,
    BUTTON_FILTER_URL,
    ButtonEligibility,
    ButtonFilterPublication,
    FightLog,
    LeaderboardEntry,
    LookingGlassMonitor,
    QuestLog,
    acceptance_has_been_handled,
    build_leaderboard,
    eligibility_rejection_reason,
    game_description,
    make_quest_claim,
    make_quest_log,
    make_rejection_post,
    parse_acceptance,
    parse_adventure_logs,
    parse_rejected_source_posts,
    points_scored_in_game,
    rejection_reason,
    render_quest_post,
    render_leaderboard,
)


class TestLookingGlassPostParsing(unittest.TestCase):

  def test_parses_case_insensitive_acceptance(self):
    post = {
        "postId": 101,
        "posterName": "Alice",
        "body": "i AcCePt [button=Aylee]",
        "deleted": False,
    }

    self.assertEqual(Acceptance(101, "Alice", "Aylee"),
                     parse_acceptance(post))

  def test_accepts_double_quoted_button_tag(self):
    post = {
        "postId": 104,
        "posterName": "Alice",
        "body": 'I accept [button="Aylee"]',
        "deleted": False,
    }

    self.assertEqual(Acceptance(104, "Alice", "Aylee"),
                     parse_acceptance(post))

  def test_accepts_single_quoted_button_tag(self):
    post = {
        "postId": 106,
        "posterName": "Alice",
        "body": "I accept [button='Aylee']",
        "deleted": False,
    }

    self.assertEqual(Acceptance(106, "Alice", "Aylee"),
                     parse_acceptance(post))

  def test_ignores_escaped_display_example(self):
    post = {
        "postId": 105,
        "posterName": "Alice",
        "body": "I accept [[]button=Aylee]",
        "deleted": False,
    }

    self.assertEqual(Acceptance(105, "Alice", ""),
                     parse_acceptance(post))

  def test_accepts_plain_button_as_remainder_of_line(self):
    post = {
        "postId": 107,
        "posterName": "Alice",
        "body": "I accept The Flying Squirrel",
        "deleted": False,
    }

    self.assertEqual(Acceptance(107, "Alice", "The Flying Squirrel"),
                     parse_acceptance(post))

  def test_accepts_quoted_plain_button(self):
    post = {
        "postId": 108,
        "posterName": "Alice",
        "body": 'I accept "Aylee"',
        "deleted": False,
    }

    self.assertEqual(Acceptance(108, "Alice", "Aylee"),
                     parse_acceptance(post))

  def test_parses_unquoted_button(self):
    post = {
        "postId": 102,
        "posterName": "Cheshire",
        "body": "I accept! [button=The Flying Squirrel]",
        "deleted": False,
    }

    self.assertEqual(Acceptance(102, "Cheshire", "The Flying Squirrel"),
                     parse_acceptance(post))

  def test_ignores_posts_without_i_accept(self):
    post = {
        "postId": 103,
        "posterName": "Alice",
        "body": "Please accept [button=Aylee]",
        "deleted": False,
    }

    self.assertIsNone(parse_acceptance(post))

  def test_ignores_i_accept_mentioned_later_in_chat(self):
    post = {
        "postId": 109,
        "posterName": "Alice",
        "body": "You should write I accept [button=Aylee] to enter.",
        "deleted": False,
    }

    self.assertIsNone(parse_acceptance(post))

  def test_ignores_i_accept_on_second_line(self):
    post = {
        "postId": 110,
        "posterName": "Alice",
        "body": "Here is an example:\nI accept [button=Aylee]",
        "deleted": False,
    }

    self.assertIsNone(parse_acceptance(post))

  def test_log_post_round_trips_through_log_parser(self):
    acceptance = Acceptance(101, "Alice", "Aylee")
    quest = make_quest_log(acceptance, 123456)
    post = {
        "postId": 555,
        "posterName": "BMAIBagels",
        "body": render_quest_post(quest),
        "deleted": False,
    }

    self.assertEqual(
        [QuestLog(
            555, 101, "Alice", "Aylee", "active",
            (FightLog(1, 123456, "Tweedledum+dee", "active"),))],
        parse_adventure_logs([post]),
    )
    self.assertIn("[game=123456]", post["body"])
    self.assertIn(
        "[forum=1368,101]source post[/forum]", post["body"])
    self.assertNotIn("[button=", post["body"])
    self.assertTrue(post["body"].startswith("[MISSION: Alice - Aylee]\n"))
    self.assertEqual("OPERATION LOOKING GLASS FIGHT 1/6",
                     game_description(1))

  def test_rejection_post_records_the_source_post(self):
    acceptance = Acceptance(104, "Alice", "Echo")
    post = {
        "posterName": "BMAIBagels",
        "body": make_rejection_post(acceptance, "BMAIBagels cannot read it"),
        "deleted": False,
    }

    self.assertEqual({104}, parse_rejected_source_posts([post]))
    self.assertIn(
        "[forum=1368,104]source post[/forum]", post["body"])

  def test_parses_legacy_log_and_prefers_it_over_duplicate_new_log(self):
    legacy_post = {
        "postId": 33725,
        "posterName": "BMAIBagels",
        "body": (
            "[code]OPERATION LOOKING GLASS | source-post=33724 | "
            "player=Bagels | button=Aylee | game=119245 | status=active[/code]"
        ),
        "deleted": False,
    }
    duplicate = make_quest_log(Acceptance(33724, "Bagels", "Aylee"), 119246)
    duplicate_post = {
        "postId": 33726,
        "posterName": "BMAIBagels",
        "body": render_quest_post(duplicate),
        "deleted": False,
    }

    logs = parse_adventure_logs([legacy_post, duplicate_post])

    self.assertEqual(1, len(logs))
    self.assertEqual(33725, logs[0].forum_post_id)
    self.assertEqual(119245, logs[0].current_fight.game_id)

  def test_thread_claim_round_trips_without_a_game(self):
    claim = make_quest_claim(Acceptance(101, "Alice", "Aylee"))
    post = {
        "postId": 556,
        "posterName": "BMAIBagels",
        "body": render_quest_post(claim),
        "deleted": False,
    }

    parsed = parse_adventure_logs([post])[0]
    self.assertEqual("creating", parsed.status)
    self.assertEqual((), parsed.fights)

  def test_mission_header_is_primary_player_and_button_identity(self):
    quest = make_quest_log(Acceptance(101, "Old Player", "Old Button"), 123)
    body = render_quest_post(quest).replace(
        "[MISSION: Old Player - Old Button]",
        "[MISSION: Current Player - Current Button]",
        1,
    )
    post = {
        "postId": 557,
        "posterName": "BMAIBagels",
        "body": body,
        "deleted": False,
    }

    parsed = parse_adventure_logs([post])[0]
    self.assertEqual("Current Player", parsed.player)
    self.assertEqual("Current Button", parsed.button)


class TestLookingGlassSafetyChecks(unittest.TestCase):

  def setUp(self):
    self.logs = [QuestLog(
        555, 101, "Alice", "Aylee", "active",
        (FightLog(1, 123456, "Tweedledum+dee", "active"),))]

  def test_rejects_already_processed_post(self):
    self.assertTrue(acceptance_has_been_handled(
        Acceptance(101, "Someone", "Other"), self.logs, set()))

  def test_rejects_already_rejected_post(self):
    self.assertTrue(acceptance_has_been_handled(
        Acceptance(202, "Someone", "Other"), [], {202}))

  def test_allows_different_player_to_use_same_button(self):
    self.assertIsNone(
        rejection_reason(Acceptance(202, "Bob", "aylee"), self.logs))

  def test_allows_player_to_have_multiple_active_adventures(self):
    reason = rejection_reason(Acceptance(202, "alice", "Other"), self.logs)
    self.assertIsNone(reason)

  def test_rejects_same_player_reusing_button_case_insensitively(self):
    reason = rejection_reason(Acceptance(202, "alice", "aylee"), self.logs)
    self.assertEqual(
        "alice has already attempted Wonderland with Aylee; each player may "
        "attempt a given button only once",
        reason,
    )

  def test_allows_player_after_completed_adventure(self):
    completed = [QuestLog(
        555, 101, "Alice", "Aylee", "failed",
        (FightLog(1, 123456, "Tweedledum+dee", "lost"),))]
    self.assertIsNone(
        rejection_reason(Acceptance(202, "Alice", "Other"), completed))

  def test_other_player_can_use_button_after_failed_adventure(self):
    failed = [QuestLog(
        555, 101, "Alice", "Aylee", "failed",
        (FightLog(1, 123456, "Tweedledum+dee", "lost"),))]
    self.assertIsNone(
        rejection_reason(Acceptance(202, "Bob", "Aylee"), failed))

  def test_other_player_can_use_button_after_survived_adventure(self):
    survived = [QuestLog(
        555, 101, "Carol", "Aylee", "survived",
        (FightLog(6, 123456, "Alice", "won"),))]
    self.assertIsNone(
        rejection_reason(Acceptance(202, "Bob", "Aylee"), survived))

  def test_player_cannot_retry_button_after_failed_adventure(self):
    failed = [QuestLog(
        555, 101, "Alice", "Aylee", "failed",
        (FightLog(1, 123456, "Tweedledum+dee", "lost"),))]
    self.assertIn(
        "already attempted Wonderland with Aylee",
        rejection_reason(Acceptance(202, "Alice", "Aylee"), failed),
    )

  def test_player_cannot_retry_button_after_surviving(self):
    survived = [QuestLog(
        555, 101, "Alice", "Aylee", "survived",
        (FightLog(6, 123456, "Alice", "won"),))]
    self.assertIn(
        "already attempted Wonderland with Aylee",
        rejection_reason(Acceptance(202, "Alice", "Aylee"), survived),
    )

  def test_rejection_post_puts_button_filter_url_on_its_own_line(self):
    body = make_rejection_post(
        Acceptance(202, "Bob", "Aylee"),
        "[button=Aylee] is unavailable",
    )
    lines = body.splitlines()
    url_line = lines.index(BUTTON_FILTER_URL)
    self.assertEqual("", lines[url_line - 1])
    self.assertEqual("", lines[url_line + 1])


class TestButtonFilterPublication(unittest.TestCase):

  @classmethod
  def setUpClass(cls):
    cls.publication = ButtonFilterPublication()

  def test_references_current_button_filter_support_rules(self):
    self.assertTrue(self.publication.eligibility("Aylee").bmaibagels_can_read)
    self.assertTrue(self.publication.eligibility("Echo").bmaibagels_can_read)
    self.assertTrue(self.publication.eligibility("Zero").bmaibagels_can_read)

  def test_unknown_button_is_not_in_publication(self):
    self.assertIsNone(self.publication.eligibility("Not A Real Button"))

  def test_rejects_unreadable_button(self):
    eligibility = ButtonEligibility("Unsupported", False, 50.0)
    self.assertIn("cannot read", eligibility_rejection_reason(eligibility))

  def test_operation_accepts_echo_as_a_safe_copying_exception(self):
    eligibility = ButtonEligibility("Echo", False, 49.13)
    self.assertIsNone(eligibility_rejection_reason(eligibility))

  def test_rejects_button_without_published_win_rate(self):
    eligibility = ButtonEligibility("New Button", True, None)
    self.assertIn("no published", eligibility_rejection_reason(eligibility))

  def test_win_rate_must_be_below_sixty_percent(self):
    self.assertIsNone(eligibility_rejection_reason(
        ButtonEligibility("Low Rated", True, 0.0)))
    self.assertIsNone(eligibility_rejection_reason(
        ButtonEligibility("Inside", True, 59.99)))
    self.assertIn("below 60%", eligibility_rejection_reason(
        ButtonEligibility("Upper Edge", True, 60.0)))


class TestCommunityLeaderboard(unittest.TestCase):

  def game(self, losses, action_messages):
    return {
        "playerDataArray": [
            {
                "playerName": "Player One",
                "gameScoreArray": {"W": 3, "L": losses, "D": 0},
            },
            {
                "playerName": "BMAIBagels",
                "gameScoreArray": {"W": losses, "L": 3, "D": 0},
            },
        ],
        "gameActionLog": [
            {"message": message} for message in action_messages
        ],
    }

  def test_points_scored_uses_wins_losses_and_draws(self):
    game = self.game(1, [
        "End of round: Player One won round 1 (50.5 vs. 20)",
        "End of round: BMAIBagels won round 2 (60 vs. 12)",
        "Round 3 ended in a draw (30 vs. 30)",
        "End of round: Player One won round 4 because opponent surrendered",
    ])

    self.assertEqual(92.5, points_scored_in_game(game, "Player One"))

  def test_ranking_uses_defeated_then_losses_then_points(self):
    quests = [
        QuestLog(1, 11, "Player One", "Aylee", "failed", (
            FightLog(1, 101, "Tweedledum+dee", "won"),
            FightLog(2, 102, "Mad Hatter", "lost"),
        )),
        QuestLog(2, 12, "Player Two", "Gluttony", "survived", (
            FightLog(1, 201, "Tweedledum+dee", "won"),
            FightLog(2, 202, "Mad Hatter", "active"),
        )),
    ]
    games = {
        101: self.game(1, [
            "End of round: Player One won round 1 (40 vs. 10)"]),
        102: self.game(3, [
            "End of round: BMAIBagels won round 1 (60 vs. 5)"]),
        201: {
            "playerDataArray": [
                {"playerName": "Player Two",
                 "gameScoreArray": {"W": 3, "L": 0, "D": 0}},
                {"playerName": "BMAIBagels",
                 "gameScoreArray": {"W": 0, "L": 3, "D": 0}},
            ],
            "gameActionLog": [
                {"message": "End of round: Player Two won round 1 (20 vs. 5)"}
            ],
        },
    }

    entries = build_leaderboard(quests, games.__getitem__)

    self.assertEqual(["Player Two", "Player One"],
                     [entry.player for entry in entries])
    self.assertEqual(0, entries[0].rounds_lost)
    self.assertEqual(4, entries[1].rounds_lost)
    self.assertEqual("Mad Hatter", entries[1].defeated_by)

  def test_active_quests_do_not_appear(self):
    active = QuestLog(2, 12, "Player Two", "Gluttony", "active", (
        FightLog(1, 201, "Tweedledum+dee", "won"),
        FightLog(2, 202, "Mad Hatter", "active"),
    ))

    self.assertEqual([], build_leaderboard([active], Mock()))

  def test_exact_scores_share_rank(self):
    entries = [
        LeaderboardEntry("A", "One", 2, 1, 100, "active"),
        LeaderboardEntry("B", "Two", 2, 1, 100, "failed"),
        LeaderboardEntry("C", "Three", 1, 0, 200, "active"),
    ]

    body = render_leaderboard(entries)

    self.assertIn("1. A", body)
    self.assertIn("1. B", body)
    self.assertIn("3. C", body)

  def test_failed_entry_names_the_opponent_who_defeated_champion(self):
    entry = LeaderboardEntry(
        "Bagels", "Aylee", 0, 3, 205, "failed", "Tweedledum+dee")

    body = render_leaderboard([entry])

    self.assertIn("DEFEATED by Tweedledum+dee", body)
    self.assertNotIn("— FAILED", body)
    self.assertNotIn("[button=", body)

  def test_defeat_groups_are_separated_by_a_blank_line(self):
    entries = [
        LeaderboardEntry(
            "One", "Aylee", 4, 2, 100, "failed", "The Jabberwock"),
        LeaderboardEntry(
            "Two", "Gluttony", 3, 1, 90, "failed", "Queen Of Hearts"),
    ]

    body = render_leaderboard(entries)

    self.assertIn("DEFEATED by The Jabberwock\n\n2. Two", body)

  def test_only_new_entries_are_bold_on_the_next_roster_change(self):
    old_entry = LeaderboardEntry(
        "One", "Aylee", 4, 2, 100, "failed", "The Jabberwock")
    new_entry = LeaderboardEntry(
        "Two", "Gluttony", 3, 1, 90, "failed", "Queen Of Hearts")
    previous_body = render_leaderboard([old_entry], bold_all=True)

    body = render_leaderboard(
        [old_entry, new_entry], previous_body=previous_body)

    self.assertIn("1. One — Aylee", body)
    self.assertNotIn("[b]1. One — Aylee", body)
    self.assertIn("[b]2. Two — Gluttony", body)


class TestQuestProgression(unittest.TestCase):

  def make_quest(self, fight_number=1, opponent="Tweedledum+dee"):
    return QuestLog(
        forum_post_id=555,
        source_post_id=101,
        player="AlicePlayer",
        button="Aylee",
        status="active",
        fights=(FightLog(fight_number, 123456, opponent, "active"),),
    )

  def completed_game(self, player_wins, state="END_GAME"):
    return {
        "gameState": state,
        "playerDataArray": [
            {
                "playerName": "AlicePlayer",
                "gameScoreArray": {"W": 3 if player_wins else 1},
            },
            {
                "playerName": "BMAIBagels",
                "gameScoreArray": {"W": 1 if player_wins else 3},
            },
        ],
    }

  def test_unresolved_active_game_is_not_loaded_or_changed(self):
    client = Mock()
    monitor = LookingGlassMonitor(client, Mock())
    quest = self.make_quest()

    self.assertEqual(
        quest, monitor.reconcile_quest(quest, [{"gameId": 123456}]))
    client.wrap_load_game_data.assert_not_called()
    client.wrap_edit_forum_post.assert_not_called()

  def test_creating_claim_adopts_matching_game_instead_of_duplicating(self):
    client = Mock()
    monitor = LookingGlassMonitor(client, Mock())
    claim = QuestLog(555, 101, "AlicePlayer", "Aylee", "creating", ())
    existing_game = {
        "gameId": 119245,
        "opponentName": "AlicePlayer",
        "myButtonName": "Tweedledum+dee",
        "opponentButtonName": "Aylee",
    }

    with TemporaryDirectory() as temporary_directory:
      pending_path = Path(temporary_directory) / "pending.json"
      with patch.object(watchwonderland, "PENDING_FILE", pending_path):
        updated = monitor.reconcile_quest(claim, [existing_game])

    self.assertEqual(119245, updated.current_fight.game_id)
    client.wrap_create_game.assert_not_called()
    client.wrap_edit_forum_post.assert_called_once()

  def test_loss_ends_quest_and_edits_same_post(self):
    client = Mock()
    client.wrap_load_game_data.return_value = self.completed_game(False)
    monitor = LookingGlassMonitor(client, Mock())

    updated = monitor.reconcile_quest(self.make_quest(), [])

    self.assertEqual("failed", updated.status)
    self.assertEqual("lost", updated.current_fight.result)
    client.wrap_edit_forum_post.assert_called_once()
    self.assertEqual(555, client.wrap_edit_forum_post.call_args.args[0])
    client.wrap_create_game.assert_not_called()

  def test_win_creates_next_fixed_fight_and_edits_same_post(self):
    client = Mock()
    client.wrap_load_game_data.return_value = self.completed_game(True)
    client.wrap_create_game.return_value = {"gameId": 234567}
    monitor = LookingGlassMonitor(client, Mock())

    with TemporaryDirectory() as temporary_directory:
      pending_path = Path(temporary_directory) / "pending.json"
      with patch.object(watchwonderland, "PENDING_FILE", pending_path):
        updated = monitor.reconcile_quest(self.make_quest(), [])

    self.assertEqual("won", updated.fights[0].result)
    self.assertEqual(FightLog(2, 234567, "Mad Hatter", "active"),
                     updated.current_fight)
    client.wrap_create_game.assert_called_once_with(
        "Mad Hatter", "Aylee", "BMAIBagels", "AlicePlayer",
        "OPERATION LOOKING GLASS FIGHT 2/6")
    self.assertEqual(555, client.wrap_edit_forum_post.call_args.args[0])

  def test_beating_alice_is_the_only_survival_state(self):
    client = Mock()
    client.wrap_load_game_data.return_value = self.completed_game(True)
    monitor = LookingGlassMonitor(client, Mock())
    quest = self.make_quest(6, "Alice")

    updated = monitor.reconcile_quest(quest, [])

    self.assertEqual("survived", updated.status)
    self.assertEqual("won", updated.current_fight.result)
    client.wrap_create_game.assert_not_called()


if __name__ == "__main__":
  unittest.main()
