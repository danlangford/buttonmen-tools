# BMAIR simulation tools

These scripts use the managed local BMAIR release. They do not create games or
submit moves to Button Weavers.

## Predict a live game

`predictgame.py` loads one game through the credentials in `.bmrc`, evaluates
the current board, and simulates complete matches between its two buttons.

```shell
./venv/bin/python predictgame.py 119401 --matches 100
```

The current-round percentage is BMAIR's position evaluation. BMAIR counts a
drawn round as half a win. The fresh-match percentage comes directly from
complete first-to-three simulations. The current-game percentage combines that
baseline with the real match score and current-round evaluation; it is labeled
as an approximation because BMAIR cannot begin `playgame` halfway through an
existing round.

The current-round favorite uses ply 3 with 5–100 simulations by default and
prints before full simulations begin. A quick score-aware game forecast prints
with it. Use `--quick` to stop there. The quick forecast uses the live round
evaluation and real W/L score, then treats any later rounds as even. Use
`--debug` to print the exact BMAIR request, recipes, executable, seed, and search
settings. The `--round-*` options control this initial evaluation independently
of the less expensive full-match settings.

Complete simulations enforce the Button Men swing rule inside BMAIR: the round
winner retains selected Swing and Option values, while the loser may select new
values before the next round.

Some complicated buttons take a long time. Full matches default to ply 2. Use
`--timeout`, reduce `--matches`, or use smaller `--max-sims` and
`--simulation-ply` values for an exploratory run. Use `--help` for every
setting.

## Compare actors with obstacles

Create two UTF-8 text files. Each nonblank line has a button name, a colon, and
a BMAI recipe. Website-style parentheses are accepted.

```text
The Tick: (6) (10) (12) (20) (X)
Aylee: p(20) s(20) (V) (X)
```

Lines beginning with `//` are comments. A line beginning with `#` and containing
no colon is also treated as a comment.

Copying buttons may use the `@opponent` recipe directive. Echo and Zero use it
to simulate with the obstacle's recipe.

Run every actor against every obstacle:

```shell
./venv/bin/python simulatebuttonfield.py actors.txt obstacles.txt \
  --matches 100 --output simulation-results/looking-glass
```

The repository includes generated OPERATION LOOKING GLASS inputs. They contain
every currently published OP:LOOKGLASS actor except the intentionally excluded
`RandomBM*` buttons, plus Echo's explicit operation exception:

```shell
./venv/bin/python simulatebuttonfield.py \
  operation-looking-glass-actors.txt \
  operation-looking-glass-obstacles.txt \
  --matches 100 --output simulation-results/looking-glass
```

Regenerate those files after Button Filter data or eligibility changes:

```shell
./venv/bin/python generate_looking_glass_lists.py
```

The match count is split across both player-slot orientations. The command
writes three files:

- `looking-glass-pairings.csv`: one row per actor/obstacle pairing;
- `looking-glass-actors.csv`: aggregate actor rankings;
- `looking-glass.json`: inputs, settings, pairings, and rankings together.

The pairing CSV is checkpointed after every completed pairing. Actor rankings
sort first by the estimated probability of beating every obstacle, calculated
as the product of independently estimated pairing win rates. The weakest and
average matchup rates are included so zero-win samples and uncertain sweep
estimates remain visible.

Long pairings are divided into `--chunk-size 50` match chunks by default. A
progress line is printed after each chunk. Each chunk contains complete games;
no individual game is split across BMAIR processes.

While one BMAIR chunk is still running, both tools print a heartbeat every 30
seconds by default. Change that with `--heartbeat`. Before each chunk starts,
the tools print its match range, player-slot orientation, and deterministic
seed. A timeout error repeats that information together with both recipes so
the slow case can be reproduced independently.
