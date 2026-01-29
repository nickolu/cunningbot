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
  "fandom", "canon", "lore", "continuity", "retcon", "multiverse", "timeline", 
  "origin story", "mythos", "worldbuilding", "easter egg", "deep cut", 
  "reference", "homage", "spoiler",

  # Sci-fi (general)
  "spaceship", "starship","hyperspace", "alien", "first contact", "robot", "android", 
  "cyborg", "AI", "singularity", "time travel", "time loop", "parallel universe", 
  "alternate timeline", "terraforming", "mecha",

  # Fantasy (general)
  "magic","magic system", "wizard", "sorcerer", "spellbook", "artifact", 
  "ancient relic", "prophecy", "chosen one", "dark lord", "dragon", "elf", 
  "dwarf", "undead", "portal fantasy", "drow", "halfling", "gnome", "half-elf", 

  # Epic / Dark fantasy
  "high fantasy", "dark fantasy", "grimdark", "blood magic", "forbidden magic", 
  "ancient evil", "fallen kingdom", "legendary weapon",

  # Tabletop RPGs
  "tabletop", "roleplaying", "campaign", "quest", "dungeon", "encounter", 
  "dice", "d20", "critical hit", "critical fail", "initiative", "hit points", 
  "character sheet", "character build", "class", "race", "alignment", "game master",
  "homebrew","one-shot","minmaxing","d&d", "dungeons and dragons",

  # Board games
  "board game", "deck building", "worker placement", "resource management", 
  "area control", "cooperative play", "legacy game", "card game", "board game",
  "deck building", "worker placement", "resource management", "area control", "cooperative play", "legacy game",
  "magic the gathering", "mtg", "pokemon", "yugioh", "magic: the gathering", "pokemon", "yugioh",

  # Video games (general)
  "video game","boss fight", "final boss", "NPC", "open world", "sandbox", "level up", 
  "experience points", "skill tree", "perk", "loot", "grind", "side quest", "fast travel", "new game plus",
  "Nintendo", "PlayStation", "Xbox", "Steam", "Epic Games", "GOG", "Origin", "Blizzard Entertainment",
  "minecraft", "fortnite", "roblox", "league of legends", "valorant", "csgo", "dota 2", "overwatch", "wow", 
  "warcraft", "starcraft", "diablo",

  # JRPGs
  "JRPG", "turn-based combat", "party system", "random encounter", "overworld", "summon", "limit break", 
  "status effect", "healer", "tank", "mage", "warrior", "rogue", "cleric", "paladin", "bard", 
  "monk", "ninja", "samurai", "ninja", "samurai", "ninja", "samurai",

  # Roguelike / Roguelite
  "procedural generation", "permadeath", "run-based", "meta progression", "random seed",

  # Indie / modern genres
  "metroidvania", "soulslike", "battle royale", "auto battler", "idle game", "visual novel", 
  "retro gaming", "arcade", "8-bit", "16-bit", "pixel art", "chiptune", "cartridge", "console war", "emulation", "romhack", "NES", 
  "SNES", "PlayStation", "Xbox", "Steam", "Epic Games", "GOG", "Origin", "Blizzard Entertainment",

  # Internet & fandom culture
  "meme","fan theory", "headcanon", "retcon", "continuity", "multiverse", "timeline", "origin story", "mythos", 
  "worldbuilding", "easter egg", "deep cut", "reference", "homage", "spoiler", "fanfiction", "fanart", "shipping", 
  "OTP", "power scaling", "tier list", "remake", "reboot", "adaptation", "crossover", "expanded universe",

  # San Diego
  "San Diego","San Diego","San Diego","San Diego","San Diego","Southern California","California",

  # Sports
  "sports","San Diego Padres","baseball","MLB","NBA","NFL","MLB All-Star Game","Olympics",

  # Linguistics
  "english","spelling","language","grammar","phonology","phonetics","morphology","syntax","semantics","pragmatics",

  # music
  "music", "punk rock", "1990s alternative rock", "1970s rock", "classic rock", "grunge music", "EDM", "hip hop", 
  "emo music", "2000s pop music", "2000s college rock", "indie rock", "indie music", "indie pop", "music news", 
  "music industry", "music recording", "the beatles", "1980s music", "2000s music"

  # Animals
  "dogs", "dog breeds", "cat", "cat breeds", "farm animals", "wild animals", "domestic animals", "animals"

  # 1990s nostalgia
  "1990s", "1990s nostalgia", "Nickelodeon", "Cartoon Network", "MTV", "VH1", "Nickelodeon", "Cartoon Network", "MTV", "VH1",
  "Beavis and Butt-Head", "Daria", "South Park", "Snick", "Animaniacs",

  # The Simpsons
  "The Simpsons", "The Simpsons characters", "The Simpsons episodes", "The Simpsons seasons", "Homer Simpson", "Bart Simpson", 
  "Lisa Simpson", "Maggie Simpson", "Marge Simpson", "Moe's Tavern", "Krusty Burger", "Krusty the Clown", "Ned Flanders", "Ralph Wiggum",
  "Milhouse Van Houten", "Nelson Muntz", 
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
    "why it matters",
    "why its famous",

    # Definition & explanation
    "definition",
    "meaning",
    "example",
    "counterexample",
    "common misconception",
    "little known fact",
    "hidden detail",
    "behind the scenes",

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
    "fan reaction",
    "community",
    "debate",
    "controversy",
    "meme",
    "trope",
    "cliche",
    "fan theory",
    "headcanon",
    "retcon",

    # Failure, bugs & weirdness
    "bug",
    "glitch",
    "exploit",
    "oversight",
    "design flaw",
    "broken version",
    "backlash",
    "misstep",
    "abandoned idea",
    "cut content",

    # Extremes & firsts
    "first",
    "last",
    "most influential",
    "most controversial",
    "most iconic",
    "best known",
    "worst known",

    # People & creation
    "creator",
    "inventor",
    "founder",
    "pioneer",
    "vision",
    "original intent"

    # Random & unusual
    "random",
    "unusual",
    "odd",
    "rare",
    "unique",
    "obscure",
    "esoteric",
    "uncommon",
    "unusual",
    "odd",
    "@#$&(*!)",
    "crazy",
    "weird",
    "strange",
    "unusual",
    "odd",
    "rare",
    "unique",
    "obscure",
    "esoteric",
    "ubelievable",

]


def generate_seed(base_words: List[str] = None, modifiers: List[str] = None) -> str:
    """
    Generate a unique seed by combining a base word with a modifier.

    Args:
        base_words: Optional custom list of base words (defaults to BASE_WORDS)
        modifiers: Optional custom list of modifiers (defaults to MODIFIERS)

    Returns:
        str: Seed in format "baseword_modifier"
    """
    words = base_words if base_words is not None else BASE_WORDS
    mods = modifiers if modifiers is not None else MODIFIERS

    base = random.choice(words)
    modifier = random.choice(mods)
    return f"{base} :: {modifier}"


def get_unused_seed(used_seeds: List[str], base_words: List[str] = None, modifiers: List[str] = None) -> str:
    """
    Get a seed that hasn't been used yet.

    Tries random selection first (fast path). If most seeds are used,
    enumerates all possibilities to find unused ones. If all seeds exhausted,
    resets the pool.

    Args:
        used_seeds: List of previously used seeds
        base_words: Optional custom list of base words (defaults to BASE_WORDS)
        modifiers: Optional custom list of modifiers (defaults to MODIFIERS)

    Returns:
        str: An unused seed
    """
    words = base_words if base_words is not None else BASE_WORDS
    mods = modifiers if modifiers is not None else MODIFIERS

    # Fast path: try random selection
    for _ in range(100):
        seed = generate_seed(words, mods)
        if seed not in used_seeds:
            return seed

    # Slow path: enumerate all possibilities
    all_seeds = [f"{base} :: {mod}" for base in words for mod in mods]
    unused = set(all_seeds) - set(used_seeds)

    if not unused:
        # All seeds exhausted - reset the pool by returning a random seed
        # The caller should handle clearing the used_seeds list
        print("WARNING: All trivia seeds exhausted. Resetting seed pool.")
        return generate_seed(words, mods)

    return random.choice(list(unused))


def get_total_possible_seeds(base_words: List[str] = None, modifiers: List[str] = None) -> int:
    """
    Get the total number of unique seeds possible.

    Args:
        base_words: Optional custom list of base words (defaults to BASE_WORDS)
        modifiers: Optional custom list of modifiers (defaults to MODIFIERS)

    Returns:
        int: Total combinations
    """
    words = base_words if base_words is not None else BASE_WORDS
    mods = modifiers if modifiers is not None else MODIFIERS
    return len(words) * len(mods)
