import unittest
from unittest.mock import Mock, patch

import bmutils
import monitor as monitor_module
from monitor import Monitor, PollBackoff


class TestPollBackoff(unittest.TestCase):

  def test_uses_aggressive_delays_then_fallback(self):
    backoff = PollBackoff([30, 30, 30, 30, 60, 60], 120)

    self.assertEqual(
        [30, 30, 30, 30, 60, 60, 120, 120],
        [backoff.next_delay() for _ in range(8)],
    )

  def test_reset_returns_to_first_delay(self):
    backoff = PollBackoff([30, 30, 60], 120)
    backoff.next_delay()
    backoff.next_delay()

    backoff.reset()

    self.assertEqual(30, backoff.next_delay())

  def test_empty_initial_schedule_preserves_fixed_monitor_delay(self):
    backoff = PollBackoff([], 120)

    self.assertEqual([120, 120], [backoff.next_delay(), backoff.next_delay()])


class TestMonitorNetworkHandling(unittest.TestCase):

  def test_startup_network_error_defers_login_to_polling_loop(self):
    client = Mock()
    client.verify_login.side_effect = bmutils.NetworkError("offline")

    instance = Monitor(client, sleep_sec=120)

    self.assertTrue(instance.login_pending)

  def test_network_error_in_one_game_continues_to_next_game(self):
    client = Mock()
    client.verify_login.return_value = True
    client.wrap_load_new_games.return_value = []
    client.wrap_load_active_games.return_value = [
        {"gameId": 41, "isAwaitingAction": 1},
        {"gameId": 42, "isAwaitingAction": 1},
    ]
    handler = Mock(side_effect=[bmutils.NetworkError("offline"), True])
    instance = Monitor(client, sleep_sec=120)

    with patch.object(
        monitor_module.time, "sleep", side_effect=StopIteration):
      with self.assertRaises(StopIteration):
        instance.start(handle_active=handler, await_confirm=False)

    self.assertEqual([41, 42], [call.args[0]["gameId"]
                               for call in handler.call_args_list])

  def test_waiting_sort_handles_longest_inactive_game_first(self):
    client = Mock()
    client.verify_login.return_value = True
    client.wrap_load_new_games.return_value = []
    client.wrap_load_active_games.return_value = [
        {"gameId": 41, "isAwaitingAction": 1, "inactivityRaw": 60},
        {"gameId": 99, "isAwaitingAction": 1, "inactivityRaw": 3600},
        {"gameId": 12, "isAwaitingAction": 1, "inactivityRaw": 600},
    ]
    handler = Mock(side_effect=StopIteration)
    instance = Monitor(client, sleep_sec=120)

    with self.assertRaises(StopIteration):
      instance.start(handle_active=handler, await_confirm=False, sort="waiting")

    self.assertEqual(99, handler.call_args.args[0]["gameId"])


if __name__ == "__main__":
  unittest.main()
