"""Seed system for generating unique trivia questions."""

import random
from typing import List

# Six categories following Trivial Pursuit style
CATEGORIES = [
    "History",
    "Science",
    "Sports",
    "Entertainment",
    "Arts & Literature",
    "Geography"
]

# Base words covering diverse topics (100+ words)
# BASE_WORDS = [
#     # People - Historical Figures
#     "einstein", "shakespeare", "napoleon", "cleopatra", "davinci", "newton", "gandhi",
#     "lincoln", "churchill", "beethoven", "mozart", "picasso", "michelangelo",
#     "columbus", "washington", "jefferson", "roosevelt", "mandela", "kingmlk",
#     "curie", "darwin", "galileo", "aristotle", "plato", "socrates", "confucius",
#     "caesar", "alexander", "genghiskhan", "charlemagne", "victoria", "elizabeth",

#     # People - Modern Figures
#     "jobs", "gates", "bezos", "musk", "zuckerberg", "winfrey", "disney",
#     "spielberg", "hitchcock", "kubrick", "tarantino", "scorsese",

#     # Places - Landmarks
#     "pyramids", "colosseum", "eiffeltower", "tajmahal", "greatwall", "statueofliberty",
#     "bigben", "operahouse", "christredeemer", "machupicchu", "petra", "angkorwat",
#     "stonehenge", "acropolis", "forbiddencity", "kremlin", "whitehouse", "louvre",

#     # Places - Geographic Features
#     "everest", "amazon", "sahara", "nile", "mississippi", "rockies", "andes",
#     "himalayas", "alps", "kilimanjaro", "grandcanyon", "niagara", "victoria",
#     "mariana", "pacific", "atlantic", "mediterranean", "caribbean",

#     # Places - Cities/Regions
#     "rome", "athens", "paris", "london", "newyork", "tokyo", "beijing",
#     "moscow", "istanbul", "jerusalem", "mecca", "cairo", "baghdad", "venice",

#     # Things - Inventions
#     "wheel", "telephone", "internet", "computer", "airplane", "automobile",
#     "television", "radio", "lightbulb", "penicillin", "printing", "compass",
#     "telescope", "microscope", "steam", "electricity", "nuclear", "laser",
#     "transistor", "vaccine", "antibiotics", "photography", "cinema",

#     # Things - Concepts
#     "democracy", "capitalism", "communism", "renaissance", "enlightenment",
#     "revolution", "evolution", "relativity", "quantum", "gravity", "dna",
#     "atom", "molecule", "photosynthesis", "magnetism", "radiation",

#     # Events - Wars & Conflicts
#     "worldwar1", "worldwar2", "civilwar", "coldwar", "vietnam", "korea",
#     "napoleonic", "trojan", "crusades", "revolution", "independence",

#     # Events - Historical Periods
#     "renaissance", "medieval", "ancient", "industrial", "victorian",
#     "roaring20s", "great depression", "prohibition", "goldrush",

#     # Sports & Games
#     "olympics", "worldcup", "superbowl", "baseball", "basketball", "football",
#     "soccer", "tennis", "golf", "boxing", "marathon", "chess",

#     # Arts & Literature
#     "hamlet", "odyssey", "illiad", "bible", "quran", "moby", "gatsby",
#     "pride", "dracula", "sherlock", "1984", "fahrenheit", "hobbit",
#     "starwars", "startrek", "marvel", "dc", "disney", "pixar",

#     # Science & Nature
#     "blackhole", "bigbang", "galaxy", "planet", "star", "comet", "asteroid",
#     "volcano", "earthquake", "tsunami", "hurricane", "tornado", "lightning",
#     "dinosaur", "mammoth", "neanderthal", "fossil", "extinction",
# ]

BASE_WORDS = [
  # Meta nerd culture
  "fandom",
  "canon",
  "lore",
  "continuity",
  "retcon",
  "multiverse",
  "timeline",
  "origin story",
  "mythos",
  "worldbuilding",
  "easter egg",
  "deep cut",
  "reference",
  "homage",
  "spoiler",

  # Sci-fi (general)
  "spaceship",
  "starship",
  "hyperspace",
  "alien",
  "first contact",
  "robot",
  "android",
  "cyborg",
  "AI",
  "singularity",
  "time travel",
  "time loop",
  "parallel universe",
  "alternate timeline",
  "terraforming",
  "mecha",

  # Cyberpunk
  "cyberpunk",
  "megacorp",
  "netrunner",
  "cyberspace",
  "augmentation",
  "neural implant",
  "hacker",
  "corporate dystopia",
  "synthetic human",
  "digital consciousness",
  "neon noir",

  # Fantasy (general)
  "magic",
  "magic system",
  "wizard",
  "sorcerer",
  "spellbook",
  "artifact",
  "ancient relic",
  "prophecy",
  "chosen one",
  "dark lord",
  "dragon",
  "elf",
  "dwarf",
  "undead",
  "portal fantasy",

  # Epic / Dark fantasy
  "high fantasy",
  "dark fantasy",
  "grimdark",
  "blood magic",
  "forbidden magic",
  "ancient evil",
  "fallen kingdom",
  "legendary weapon",

  # Tabletop RPGs
  "tabletop",
  "roleplaying",
  "campaign",
  "quest",
  "dungeon",
  "encounter",
  "dice",
  "d20",
  "critical hit",
  "critical fail",
  "initiative",
  "hit points",
  "character sheet",
  "character build",
  "class",
  "race",
  "alignment",
  "game master",
  "homebrew",
  "one-shot",
  "minmaxing",

  # Board games
  "board game",
  "deck building",
  "worker placement",
  "resource management",
  "area control",
  "cooperative play",
  "legacy game",

  # Video games (general)
  "video game",
  "boss fight",
  "final boss",
  "NPC",
  "open world",
  "sandbox",
  "level up",
  "experience points",
  "skill tree",
  "perk",
  "loot",
  "grind",
  "side quest",
  "fast travel",
  "new game plus",

  # JRPGs
  "JRPG",
  "turn-based combat",
  "party system",
  "random encounter",
  "overworld",
  "summon",
  "limit break",
  "status effect",
  "healer",
  "tank",

  # Roguelike / Roguelite
  "roguelike",
  "roguelite",
  "procedural generation",
  "permadeath",
  "run-based",
  "meta progression",
  "random seed",

  # Indie / modern genres
  "metroidvania",
  "soulslike",
  "battle royale",
  "auto battler",
  "idle game",
  "visual novel",
  "walking simulator",

  # Retro gaming
  "arcade",
  "8-bit",
  "16-bit",
  "pixel art",
  "chiptune",
  "cartridge",
  "console war",
  "emulation",
  "romhack",

  # Speedrunning / challenge culture
  "speedrun",
  "any percent",
  "glitchless",
  "sequence break",
  "exploit",
  "softlock",
  "hardlock",

  # Dev / programming culture
  "programming",
  "code",
  "algorithm",
  "data structure",
  "bug",
  "debugging",
  "refactor",
  "technical debt",
  "open source",
  "repository",
  "commit",
  "branch",
  "merge",
  "API",
  "framework",
  "library",
  "engine",

  # Internet & fandom culture
  "meme",
  "copypasta",
  "fan theory",
  "headcanon",
  "fanfiction",
  "fanart",
  "shipping",
  "OTP",
  "power scaling",
  "tier list",
  "remake",
  "reboot",
  "adaptation",
  "crossover",
  "expanded universe"
]

# Modifiers to create context and variation (50+ modifiers)
MODIFIERS = [
    # History & time
    "origin",
    "creation",
    "rise",
    "fall",
    "peak",
    "decline",
    "renaissance",
    "golden_age",
    "turning_point",
    "milestone",
    "timeline",
    "evolution",

    # Significance & impact
    "impact",
    "significance",
    "legacy",
    "influence",
    "reputation",
    "why_it_matters",
    "why_its_famous",

    # Definition & explanation
    "definition",
    "meaning",
    "example",
    "counterexample",
    "common_misconception",
    "little_known_fact",
    "hidden_detail",
    "behind_the_scenes",

    # Comparison & contrast
    "difference",
    "similarity",
    "contrast",
    "variant",
    "alternative",
    "inspiration",
    "predecessor",
    "successor",

    # Mechanics, systems & design
    "mechanic",
    "rule",
    "system",
    "design",
    "balance",
    "strategy",
    "tactic",
    "optimization",
    "tradeoff",
    "limitation",
    "constraint",

    # Culture, fandom & meta
    "fan_reaction",
    "community",
    "debate",
    "controversy",
    "meme",
    "trope",
    "cliche",
    "fan_theory",
    "headcanon",
    "retcon",

    # Failure, bugs & weirdness
    "bug",
    "glitch",
    "exploit",
    "oversight",
    "design_flaw",
    "broken_version",
    "backlash",
    "misstep",
    "abandoned_idea",
    "cut_content",

    # Extremes & firsts
    "first",
    "last",
    "most_influential",
    "most_controversial",
    "most_iconic",
    "best_known",
    "worst_known",

    # People & creation
    "creator",
    "inventor",
    "founder",
    "pioneer",
    "vision",
    "original_intent"
]


def generate_seed() -> str:
    """
    Generate a unique seed by combining a base word with a modifier.

    Returns:
        str: Seed in format "baseword_modifier"
    """
    base = random.choice(BASE_WORDS)
    modifier = random.choice(MODIFIERS)
    return f"{base}_{modifier}"


def get_unused_seed(used_seeds: List[str]) -> str:
    """
    Get a seed that hasn't been used yet.

    Tries random selection first (fast path). If most seeds are used,
    enumerates all possibilities to find unused ones. If all seeds exhausted,
    resets the pool.

    Args:
        used_seeds: List of previously used seeds

    Returns:
        str: An unused seed
    """
    # Fast path: try random selection
    for _ in range(100):
        seed = generate_seed()
        if seed not in used_seeds:
            return seed

    # Slow path: enumerate all possibilities
    all_seeds = [f"{base}_{mod}" for base in BASE_WORDS for mod in MODIFIERS]
    unused = set(all_seeds) - set(used_seeds)

    if not unused:
        # All seeds exhausted - reset the pool by returning a random seed
        # The caller should handle clearing the used_seeds list
        print("WARNING: All trivia seeds exhausted. Resetting seed pool.")
        return generate_seed()

    return random.choice(list(unused))


def get_total_possible_seeds() -> int:
    """
    Get the total number of unique seeds possible.

    Returns:
        int: Total combinations
    """
    return len(BASE_WORDS) * len(MODIFIERS)
