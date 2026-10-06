# Configurable adventure watcher

`watchadventure.py` is the config-driven successor to `watchwonderland.py`.
The old watcher is independent and does not import or share runtime state with
the new one.

## Validate without contacting Button Weavers

```sh
venv/bin/python watchadventure.py adventures/operation-looking-glass.toml --validate-config
venv/bin/python watchadventure.py adventures/operation-oz.toml --validate-config
venv/bin/python watchadventure.py adventures/example-adventure.toml --validate-config
```

Validation reads the TOML file and the local Button Filter publication so it
can verify every opponent. It does not log in, use the network, load a thread,
create games, or write forum posts.

## Start an adventure

```sh
venv/bin/python watchadventure.py adventures/example-adventure.toml
```

Before starting a copied example:

1. Set a unique `slug` and the real `thread_id`.
2. Choose `button_uniqueness`: `global`, `player`, or `none`.
3. Set optional inclusive `minimum_win_rate` and exclusive
   `maximum_win_rate` limits.
4. List the ordered opponent buttons under `[[fights]]`.
5. Review the credentials, Button Filter, state-directory, and sleep settings.
6. If using a leaderboard, reserve a forum post, set `enabled = true`, and set
   its `post_id`.
7. Rewrite any desired text under `[templates]` while preserving valid
   `{placeholders}`.

Each fight may include an optional human-readable `description`. The watcher
automatically appends that story beat after the stable `game_description`
identity:

```toml
[[fights]]
opponent_button = "Aylee"
label = "the Clockwork Knight"
description = "Pass the knight who guards the inner mechanism."

[templates]
game_description = "MY ADVENTURE {fight_number}/{fight_count} | MISSION {source_post_id}"
```

Keep changing prose out of `game_description`: it is the stable identity used
to recover games after a restart. Fight descriptions may be edited safely. The
watcher recognizes both an unflavored identity and the same identity followed
by ` | ` and descriptive text.

When `button_uniqueness = "none"`, the `game_description` template must retain
`{source_post_id}`. That mission ID lets the watcher distinguish simultaneous
games involving the same player and button after a restart.

In `forum_post` mode, the watcher accepts only posts whose first line starts
with the configured `acceptance_command`. It makes a persistent forum claim before creating the
first game. Each mission post ends with a compact `ADVENTURE_RECORD_V1` JSON
record, so display text may change without breaking state recovery.

## Completed-game entry mode

Set `[entry] mode = "completed_game"` to make a completed game against the
first configured opponent serve as fight 1. `games_created_after` establishes
the history cutoff and `target_wins` requires the intended match length. The
earliest game ID wins when the uniqueness rule identifies duplicates.

To stop accepting new entries without stranding existing missions, add an
exclusive UTC cutoff:

```toml
[entry]
mode = "completed_game"
games_created_after = "2026-10-01"
submissions_close_at = "2026-11-01T00:00:00Z"
target_wins = 3
```

An entry game started before `submissions_close_at` remains eligible even if it
finishes later. Games started at or after the cutoff are ineligible. The watcher
continues rebuilding and advancing every accepted mission from the full fixed
history window. Once the cutoff has passed and neither an accepted mission nor
an opening game remains active, the watcher logs a completion message and exits
cleanly. Removing the setting before that point reopens submissions.

A forum-post adventure can close the same way:

```toml
[entry]
submissions_close_at = "2026-11-01T00:00:00Z"
```

An `I accept` posted at or after the cutoff is ignored, with no reply.
Missions accepted earlier keep playing, and the watcher keeps running.

The completed-game mode creates no acceptance or rejection posts. Ineligible entry games may
be listed at the bottom of the leaderboard with
`show_ineligible_games = true`. It rebuilds mission state from completed game
history, generated game descriptions, and `previousGameId` links. The forum
leaderboard contains human-readable text and a format marker, not serialized
mission state.

Every generated fight copies chat from the preceding fight. Keep
`{entry_game_id}` in the completed-game mode's `game_description` so generated
games remain associated with the correct entry.

## Button uniqueness

- `global`: a button can be attempted only once across the adventure.
- `player`: each player can attempt a given button once; different players can
  use the same button.
- `none`: prior button use never blocks an acceptance.

These rules do not limit how many different adventures a player can have active
at once.

## Leaderboard behavior

On each poll the watcher computes the complete desired leaderboard text and
edits the reserved post only when that text differs. Forum-post adventures list
terminal missions. Completed-game adventures can also list active missions and
ineligible entry games. There is no incremental formatting or old-entry parsing.

## Operation Looking Glass migration

The supplied Looking Glass config enables `import_looking_glass_v2`. This reads
the standardized mission and rejection records already in thread 1368. When a
legacy mission next advances or finishes, the same forum post is rewritten with
the generic V1 JSON record. New adventures should leave this option false.

Do not run `watchwonderland.py` and `watchadventure.py` against thread 1368 at
the same time. Stop the old watcher before switching Looking Glass to the new
one.

## Running Looking Glass and OZ together

After stopping the old `watchwonderland.py`, start each adventure in its own
terminal from this repository:

```sh
# Terminal 1: thread 1368
venv/bin/python watchadventure.py adventures/operation-looking-glass.toml

# Terminal 2: thread 1372
venv/bin/python watchadventure.py adventures/operation-oz.toml
```

The two processes use different thread IDs, slugs, and lock files. They may run
at the same time. Do not start the OZ command until its configuration validates
successfully and the announcement and leaderboard posts contain the intended
starting text.
