#!/usr/bin/env python3
# MONITOR
# Example script which provides "monitor" functionality, polling
# periodically for games which are waiting for you to act

import argparse
import random
import sys
import time
from builtins import input
from datetime import datetime

import bmutils


class PollBackoff:
  """Return short polling delays first, then settle on a fallback delay."""

  def __init__(self, initial_delays, fallback_delay):
    self.initial_delays = tuple(initial_delays)
    self.fallback_delay = fallback_delay
    self.reset()

  def reset(self):
    """Start the aggressive polling sequence again after game activity."""
    self.next_index = 0

  def next_delay(self):
    if self.next_index < len(self.initial_delays):
      delay = self.initial_delays[self.next_index]
      self.next_index += 1
      return delay
    return self.fallback_delay


def parse_args():
  parser = argparse.ArgumentParser()
  parser.add_argument(
      "-c",
      "--config",
      help="config file containing site parameters",
      type=str,
      default=".bmrc",
  )
  parser.add_argument(
      "-s", "--site", help="buttonmen site to access", type=str, default="www")
  return parser.parse_args()


class Monitor(object):

  def __init__(self, client, sleep_sec=120, initial_sleep_delays=None):
    self.client = client
    self.sleep_sec = sleep_sec
    self.poll_backoff = PollBackoff(
        initial_sleep_delays or (), sleep_sec)
    self.login_pending = False
    try:
      if not self.client.verify_login():
        print("Could not login")
        sys.exit(1)
    except bmutils.NetworkError as error:
      self.login_pending = True
      print(
          f"Network error verifying login; will retry from the monitor "
          f"loop: {error}")

  def start(
      self,
      handle_new=lambda g: None,
      handle_active=lambda g: None,
      await_confirm=True,
      sort="ASC",
      filter="all",
      max=-1,
  ):

    sort = sort.upper()
    sort = "SHUFFLE" if sort == "RANDOM" else sort

    while True:

      if self.login_pending:
        try:
          if not self.client.verify_login():
            print("Could not login")
            sys.exit(1)
          self.login_pending = False
        except bmutils.NetworkError as error:
          sleep_sec = self.poll_backoff.next_delay()
          print(
              f"Network error verifying login; retrying in {sleep_sec} "
              f"seconds: {error}")
          time.sleep(sleep_sec)
          continue

      try:
        newgames = self.client.wrap_load_new_games()
      except bmutils.NetworkError as error:
        print(f"Network error loading new games; will retry next poll: {error}")
        newgames = []

      if sort == "SHUFFLE":
        random.shuffle(newgames)
      elif sort == "DESC":
        newgames.reverse()

      for ng in newgames:
        if ((filter == "all") or (filter == "odd" and ng["gameId"] % 2 != 0) or
            (filter == "even" and ng["gameId"] % 2 == 0)):
          if ng["isAwaitingAction"]:
            print(f"{ng['gameId']}: "
                  f"{self.client.username} ({ng['myButtonName']})"
                  " vs. "
                  f"{ng['opponentName']} ({ng['opponentButtonName']})")
            try:
              handle_new(ng)
            except bmutils.NetworkError as error:
              print(
                  f"Network error handling new game {ng['gameId']}; "
                  f"continuing: {error}")

      try:
        games = self.client.wrap_load_active_games()
      except bmutils.NetworkError as error:
        print(
            f"Network error loading active games; will retry next poll: "
            f"{error}")
        games = []

      if sort == "SHUFFLE":
        random.shuffle(games)
      elif sort == "DESC":
        games.reverse()
      elif sort == "WAITING":
        games.sort(key=lambda game: game.get("inactivityRaw", 0), reverse=True)

      # TODO consider instead of counting up to a max of "goog" games
      # maybe the monitor should have access to the bad_game list
      # and filter them away before executing the handler

      count=0
      games_active = False
      for game in games:
        if max > 0 and count >= max:
          print(f"count {count} reached max {max}")
          break
        if ((filter == "all") or
            (filter == "odd" and game["gameId"] % 2 != 0) or
            (filter == "even" and game["gameId"] % 2 == 0)):
          if game["isAwaitingAction"]:
            games_active = True
            try:
              handled = handle_active(game)
            except bmutils.NetworkError as error:
              print(
                  f"Network error handling game {game['gameId']}; "
                  f"continuing to the next game: {error}")
              handled = False
            if handled:
              count=count+1
              self.poll_backoff.reset()

      if games_active and await_confirm:
        input()
      else:
        # we should sleep if we dont have a max we are counting to
        # or if we have not reached the max yet
        # which is to say we exhausted the list without hitting our max
        # meaning there is a small number of playable games right now
        if max<=0 or count<max:
          sleep_sec = self.poll_backoff.next_delay()
          print(
              f"Zzz for {sleep_sec} @ {datetime.isoformat(datetime.now())}")
          time.sleep(sleep_sec)


if __name__ == "__main__":
  args = parse_args()
  bmclient = bmutils.BMClientParser(args.config, args.site)
  mon = Monitor(bmclient)
  mon.start()
