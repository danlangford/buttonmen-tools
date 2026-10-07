# BMAIBagels

The bot that plays as BMAIBagels on buttonweavers.com. It sends each game to
[BMAIR](https://github.com/danlangford/bmai/tree/rust) for a move, and keeps
itself on the newest BMAIR release.

## Setup

```shell
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
cp .bmrc.example .bmrc   # then fill in the logins
```

## Run

```shell
./venv/bin/python ./bmaibagels.py --site bmaibagels --count 1
```

`--decision-log DIR` saves what BMAIR is sent and says for every decision, as
`DIR/{game_id}-{UTC time}-input.txt` and `-output.txt`. It is off by default
and never trims itself; on the live host a systemd timer from home-ansible
keeps the directory under its size cap.

The adventure and wonderland watchers are described in
[ADVENTURE_WATCHER.md](ADVENTURE_WATCHER.md); the simulation tools in
[SIMULATION_TOOLS.md](SIMULATION_TOOLS.md). They read the ButtonFilter data
from this repo's `public/` folder.

## Tests

```shell
./venv/bin/python -m unittest bmaibagelstest bmair_release_test monitortest \
  simulationtools_test watchadventure_test watchwonderland_test
```
