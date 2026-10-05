#!/usr/bin/env python3
"""Generate simulation input lists from Button Filter's current publication."""

import argparse
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
DEFAULT_BUTTONMEN_TOOLS = ROOT.parent
OPPONENTS = (
    "Tweedledum+dee",
    "Mad Hatter",
    "White Rabbit",
    "Queen Of Hearts",
    "The Jabberwock",
    "Alice",
)
DYNAMIC_RECIPES = {
    "Echo": "@opponent",
    "Zero": "@opponent",
}


def parse_args(argv=None):
  parser = argparse.ArgumentParser()
  parser.add_argument(
      "--buttonmen-tools", type=Path, default=DEFAULT_BUTTONMEN_TOOLS)
  parser.add_argument(
      "--actors", type=Path,
      default=ROOT / "operation-looking-glass-actors.txt")
  parser.add_argument(
      "--obstacles", type=Path,
      default=ROOT / "operation-looking-glass-obstacles.txt")
  return parser.parse_args(argv)


def javascript_array(source, variable):
  match = re.search(
      rf"\b{re.escape(variable)}\s*=\s*(\[[^;]*?\])", source, re.DOTALL)
  if not match:
    raise ValueError(f"Could not find {variable} in ButtonFilter.html")
  return json.loads(match.group(1))


def load_json(path):
  return json.loads(path.read_text(encoding="utf-8"))["data"]


def published_buttons(public_directory):
  implemented = load_json(public_directory / "buttondata.json")
  unimplemented = load_json(public_directory / "buttonunimpl.json")
  buttons = {}
  for button in implemented + unimplemented:
    buttons.setdefault(button["buttonName"], button)
  return buttons


def eligible_actors(buttonmen_tools):
  public = buttonmen_tools / "public"
  filter_source = (public / "ButtonFilter.html").read_text(encoding="utf-8")
  supported_names = set(javascript_array(
      filter_source, "bmaibagels_supported_button_name"))
  unsupported_names = set(javascript_array(
      filter_source, "bmaibagels_unsupported_button_name"))
  unsupported_sets = set(javascript_array(
      filter_source, "bmaibagels_unsupported_button_set"))
  supported_features = set(javascript_array(
      filter_source, "bmaibagels_supported_die_features"))
  buttons = published_buttons(public)
  stats = load_json(public / "buttonstats.json")

  eligible = []
  for name, button in buttons.items():
    if name.startswith("RandomBM"):
      continue
    rate = stats.get(name, {}).get("rate")
    features = set(button.get("dieSkills", ()) + button.get("dieTypes", ()))
    bot_supported = (
        name in supported_names
        or (name not in unsupported_names
            and button.get("buttonSet") not in unsupported_sets
            and features <= supported_features))
    if rate is not None and rate < 60 and (bot_supported or name == "Echo"):
      recipe = button.get("recipe") or DYNAMIC_RECIPES.get(name)
      if not recipe:
        raise ValueError(
            f"Eligible dynamic button {name!r} has no simulation recipe rule")
      eligible.append((name, recipe))
  return sorted(eligible, key=lambda item: item[0].casefold()), buttons


def write_button_list(path, heading, entries):
  path.parent.mkdir(parents=True, exist_ok=True)
  lines = [f"// {heading}"]
  lines.extend(f"{name}: {recipe}" for name, recipe in entries)
  path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv=None):
  args = parse_args(argv)
  actors, buttons = eligible_actors(args.buttonmen_tools)
  obstacles = []
  for name in OPPONENTS:
    try:
      recipe = buttons[name]["recipe"]
    except KeyError as error:
      raise ValueError(f"Missing published obstacle {name!r}") from error
    obstacles.append((name, recipe))

  write_button_list(
      args.actors,
      "OPERATION LOOKING GLASS actors; generated from Button Filter OP:LOOKGLASS eligibility",
      actors,
  )
  write_button_list(
      args.obstacles,
      "OPERATION LOOKING GLASS opponents, in mission order",
      obstacles,
  )
  print(f"Wrote {len(actors)} actors to {args.actors}")
  print(f"Wrote {len(obstacles)} obstacles to {args.obstacles}")


if __name__ == "__main__":
  main()
