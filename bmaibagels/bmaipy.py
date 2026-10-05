bmai_supported_skills = {

    "Auxiliary",  # +X
    # "Boom" is enabled per binary by BMAIR_CAPABILITY_GATED_SKILLS (BMAIR 0.17.0+).
     "Doppelganger",  # DX
     "Fire",  # FX
     "Jolt",  # JX
     "Konstant",  # kX
    # "Mad" is enabled per binary by BMAIR_CAPABILITY_GATED_SKILLS (BMAIR 0.17.0+).
    "Rage",  # GX
    # "Rush" is enabled per binary by BMAIR_CAPABILITY_GATED_SKILLS (BMAIR 0.14.0+).
    # "Wildcard", # C

    "Berserk",  # BX
    "Chance",  # cX
    "Focus",  # fX
    "Insult",  # IX
    "Maximum",  # MX
    "Mighty",  # HX
    "Mood",  # X?
    "Morphing",  # mX
    "Null",  # nX
    "Ornery",  # oX
    "Poison",  # pX
    "Queer",  # qX
    "Radioactive",  # %X (implemented in BMAIR 0.15.0; parsing-only before)
    "Reserve",  # rX
    "Shadow",  # sX
    "Slow",  # wX
    "Speed",  # zX
    "Stealth",  # dX
    "Stinger",  # gX
    "TimeAndSpace",  # ^X
    "Trip",  # tX
    "Turbo",  # X!
    "Value", # vX
    "Warrior",  # `X
    "Weak",  # hX
    "P Swing",  # P
    "R Swing",  # R
    "S Swing",  # S
    "T Swing",  # T
    "U Swing",  # U
    "V Swing",  # V
    "W Swing",  # W
    "X Swing",  # X
    "Y Swing",  # Y
    "Z Swing",  # Z
    "Twin",  # (X,X)
    "Option",  # X/X

    "Q Swing",  # Q ##not on buttonweavers
    "Unique",  # uX ##not on buttonweavers
    "Unskilled",  # ~X
}

# ButtonWeavers button specials, sent with BMAIR's `special` command (0.16.0).
# Without that command these buttons are refused, since BMAIR would ignore
# their rules.
BUTTON_SPECIALS = {
    "Giant": ("no_initiative",),
    "Gordo": ("unique_sizes",),
    "Guillermo": ("unique_swing",),
    "Largo": ("no_skill_attacks",),
    "Oregon": ("unique_swing",),
    "The Flying Squirrel": ("no_skill_attacks",),
    "The Japanese Beetle": ("skill_immune",),
}

# Skills BMAIBagels accepts only when the BMAIR binary's --capabilities lists
# them, so a managed binary older than the skill keeps rejecting those games.
BMAIR_CAPABILITY_GATED_SKILLS = frozenset({
    "Boom",  # bX, BMAIR 0.17.0
    "Mad",  # X&, BMAIR 0.17.0
    "Rush",  # #X, BMAIR 0.14.0
})

# Keep aligned with ButtonFilter, except that Python evaluates Echo's generated dice.
bmai_unsupported_buttons = set()

# ButtonWeavers BMBtnSkillRandomBM draws from every die skill except Auxiliary,
# Reserve, Warrior, Doppelganger, Turbo, Morphing, Radioactive, Fire, and Slow.
RANDOMBM_SKILL_POOL = frozenset({
    "Berserk", "Boom", "Chance", "Focus", "Insult", "Jolt", "Konstant", "Mad",
    "Maximum", "Mighty", "Mood", "Null", "Ornery", "Poison", "Queer", "Rage",
    "Rush", "Shadow", "Speed", "Stealth", "Stinger", "TimeAndSpace", "Trip",
    "Value", "Weak",
})

# A challenge cannot show which skills these drew, so they are accepted only
# when the binary supports the whole pool (BMAIR 0.17.0+).
RANDOMBM_POOL_BUTTONS = frozenset({
    "RandomBMMixed",
    "RandomBMMonoskill",
    "RandomBMDuoskill",
    "RandomBMTriskill",
    "RandomBMTetraskill",
    "RandomBMPentaskill",
})

nala_supported_skills = {
    # "Fire", # FX
    # "Jolt", # JX
    # "Rush", # #X
    # "Turbo", # X!
    # "Warrior", # `X
    # "Wildcard", # C

    # "Q Swing",  # Q ##not on buttonweavers
    # "Unique",  # uX ##not on buttonweavers

    "Auxiliary",  # +X
    "Berserk",  # BX
    "Boom",  # bX
    "Chance",  # cX
    "Doppleganger",  # DX
    "Focus",  # fX
    "Insult",  # IX
    "Konstant",  # kX
    "Mad",  # X&
    "Maximum",  # MX
    "Mighty",  # HX
    "Mood",  # X?
    "Morphing",  # mX
    "Null",  # nX
    "Ornery",  # oX
    "Poison",  # pX
    "Queer",  # qX
    "Radioactive",  # %X
    "Rage",  # GX
    "Reserve",  # rX
    "Shadow",  # sX
    "Slow",  # wX
    "Speed",  # zX
    "Stealth",  # dX
    "Stinger",  # gX
    "TimeAndSpace",  # ^X
    "Trip",  # tX
    "Value",  # vX
    "Weak",  # hX
    "P Swing",  # P
    "R Swing",  # R
    "S Swing",  # S
    "T Swing",  # T
    "U Swing",  # U
    "V Swing",  # V
    "W Swing",  # W
    "X Swing",  # X
    "Y Swing",  # Y
    "Z Swing",  # Z
    "Twin",  # (X,X)
    "Option",  # X/X
}


class BMAI:

  def __init__(self) -> None:
    super().__init__()
