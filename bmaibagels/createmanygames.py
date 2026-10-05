#!/usr/env python3

import bmutils

soldiers = [
    "Avis",
    "Bauer",
    "Changeling",
    "Clare",
    "Hammer",
    "Hannah",
    "Iago",
    "Karl",
    "Kith",
    "Kublai",
    "Niles",
    "Shore",
    "Stark",
]

the_core = [
    "Delia",
    "Donna",
    "Ferrer",
    "Hamilton",
    "Hollis",
    "Janet",
    "Lady B",
    "Polly",
    "Porter",
    "Smith",
    "Stefano",
    "Steve (The Core)",
    "Tanya",
    "Tony (The Core)",
    "Wallace",
]

client = bmutils.BMClientParser(".bmrc", "www")
client.verify_login()
count = 0


def doit():
  global count
  print(f"hi {client.username}")
  for b1 in the_core:
    for b2 in the_core:
      if b1 != b2:
        continue
      count += 1
      print(count)
      if count > 0:
        r0 = print(
            client.wrap_create_game(b1, b2, "BMAIBagels", "Nala", "bot fight!"))
        # r1=print(client.wrap_create_game(b2, b1, "BMAIBagels", "Nala", "bot fight!"))
        print(f"g1={r0}")  # g2={r1}")
        print(count)

    # SOLDIERS (156)

    # starts at game 92314 #1
    # made it to game 92440  #127
    # printed 127 then Error
    # tried again, after 127
    # picks up at game 92441 #128
    # ends at game 92469 #156

    # THE CORE (210)

    # start at 92470 #1
    # ends at 92679 #210

    # then for fun mirror matches that probably wont go into "tournament results"
    # 92680-92692
    # 92693-92707


if __name__ == "__main__":
  doit()
