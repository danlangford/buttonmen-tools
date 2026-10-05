import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bmair_simulation import (
    ButtonSpec,
    build_match_request,
    infer_round_probability,
    match_win_probability,
    MatchSimulation,
    materialize_button,
    normalize_recipe,
    parse_match_simulation,
    parse_win_percent,
    read_button_list,
    simulate_balanced_matchup,
)
from predictgame import parse_args as parse_predict_args
from simulatebuttonfield import actor_summaries, parse_args as parse_field_args


class TestButtonLists(unittest.TestCase):

  def test_reads_names_and_normalizes_site_recipes(self):
    with TemporaryDirectory() as directory:
      path = Path(directory) / "buttons.txt"
      path.write_text(
          "// candidates\n"
          "The Jabberwock: (20) (20) (30) ng(30) (U)\n"
          "Steven Universe: (6) (6) M(10) (16) (X)!\n",
          encoding="utf-8",
      )

      self.assertEqual([
          ButtonSpec("The Jabberwock", "20 20 30 ng30 U"),
          ButtonSpec("Steven Universe", "6 6 M10 16 X!"),
      ], read_button_list(path))

  def test_rejects_duplicate_names_case_insensitively(self):
    with TemporaryDirectory() as directory:
      path = Path(directory) / "buttons.txt"
      path.write_text("Aylee: 6\naylee: 8\n", encoding="utf-8")

      with self.assertRaisesRegex(ValueError, "duplicate button"):
        read_button_list(path)

  def test_preserves_twin_parentheses_and_converts_selected_swings(self):
    self.assertEqual(
        "p(6,6) X!-12 6/20-20 pX? oZ!",
        normalize_recipe(
            "p(6,6) (X=12)! (6/20=20) p?(X) o!(Z)"),
    )

  def test_materializes_copy_opponent_buttons(self):
    opponent = ButtonSpec("Obstacle", "6 X")

    self.assertEqual(
        ButtonSpec("Echo", "6 X"),
        materialize_button(ButtonSpec("Echo", "@opponent"), opponent, 17),
    )


class TestBmairSimulation(unittest.TestCase):

  def test_full_match_tools_default_to_ply_two(self):
    self.assertEqual(2, parse_predict_args(["42"]).simulation_ply)
    self.assertEqual(
        2, parse_field_args(["actors.txt", "obstacles.txt"]).ply)

  def test_builds_complete_first_to_three_match_request(self):
    request = build_match_request(
        ButtonSpec("Actor", "6 X"),
        ButtonSpec("Obstacle", "8 10"),
        25,
        seed=17,
    )

    self.assertIn("game 3\npreround\n", request)
    self.assertIn("player 0 2 0\n6\nX\n", request)
    self.assertIn("player 1 2 0\n8\n10\n", request)
    self.assertTrue(request.endswith("playgame 25\nquit\n"))

  def test_parses_last_match_summary_and_current_round_percentage(self):
    result = parse_match_simulation(
        "matches over 2 - 3\nnoise\nmatches over 61 - 39\n")

    self.assertEqual(61, result.player0_wins)
    self.assertEqual(39, result.player1_wins)
    self.assertEqual(73.5, parse_win_percent(
        "l1 p0 best move (2.0 points, 73.5% win)\n"))

  def test_iid_game_probability_and_inverse_agree(self):
    game_probability = match_win_probability(0.6, 0, 0, 3)

    self.assertAlmostEqual(0.68256, game_probability)
    self.assertAlmostEqual(0.6, infer_round_probability(game_probability), 8)

  @patch("bmair_simulation.simulate_matches")
  def test_balanced_simulation_reports_complete_chunk_progress(self, simulate):
    simulate.side_effect = lambda binary, player0, player1, games, **settings: (
        MatchSimulation(games, 0, "done"))
    progress = []
    starts = []

    actor_wins, obstacle_wins, _ = simulate_balanced_matchup(
        "bmair",
        ButtonSpec("Actor", "6"),
        ButtonSpec("Obstacle", "8"),
        120,
        chunk_size=50,
        starting=lambda number, total, orientation, seed, size: starts.append(
            (number, total, orientation, size)),
        progress=lambda done, total, wins, losses: progress.append(
            (done, total, wins, losses)),
    )

    self.assertEqual((60, 60), (actor_wins, obstacle_wins))
    self.assertEqual([
        (1, 120, "forward", 50),
        (51, 120, "forward", 10),
        (61, 120, "reverse", 50),
        (111, 120, "reverse", 10),
    ], starts)
    self.assertEqual([50, 60, 110, 120], [item[0] for item in progress])


class TestActorSummary(unittest.TestCase):

  def test_ranks_by_probability_of_sweeping_all_obstacles(self):
    actors = [ButtonSpec("Steady", "6"), ButtonSpec("Swingy", "8")]
    obstacles = [ButtonSpec("One", "10"), ButtonSpec("Two", "12")]
    pairings = [
        {"actor": "Steady", "matches": 100, "actor_wins": 60,
         "obstacle_wins": 40, "actor_win_rate": 0.6},
        {"actor": "Steady", "matches": 100, "actor_wins": 60,
         "obstacle_wins": 40, "actor_win_rate": 0.6},
        {"actor": "Swingy", "matches": 100, "actor_wins": 90,
         "obstacle_wins": 10, "actor_win_rate": 0.9},
        {"actor": "Swingy", "matches": 100, "actor_wins": 30,
         "obstacle_wins": 70, "actor_win_rate": 0.3},
    ]

    summaries = actor_summaries(actors, obstacles, pairings)

    self.assertEqual("Steady", summaries[0]["actor"])
    self.assertAlmostEqual(0.36, summaries[0]["estimated_sweep_probability"])


if __name__ == "__main__":
  unittest.main()
