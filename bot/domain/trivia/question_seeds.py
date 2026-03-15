"""Seed system for generating unique trivia questions."""

import random
from typing import List, NamedTuple, Optional

from bot.app.utils.logger import get_logger

logger = get_logger()


# 24 OpenTDB category names
CATEGORIES = [
    "General Knowledge",
    "Entertainment: Books",
    "Entertainment: Film",
    "Entertainment: Music",
    "Entertainment: Musicals & Theatres",
    "Entertainment: Television",
    "Entertainment: Video Games",
    "Entertainment: Board Games",
    "Science & Nature",
    "Science: Computers",
    "Science: Mathematics",
    "Mythology",
    "Sports",
    "Geography",
    "History",
    "Politics",
    "Art",
    "Celebrities",
    "Animals",
    "Vehicles",
    "Entertainment: Comics",
    "Science: Gadgets",
    "Entertainment: Japanese Anime & Manga",
    "Entertainment: Cartoon & Animations",
]


class SeedResult(NamedTuple):
    seed: str
    category: str


# Seeds organized by category
CATEGORIZED_SEEDS = {
    "General Knowledge": [
        "fandom", "canon", "lore", "continuity", "multiverse", "timeline",
        "origin story", "mythos", "worldbuilding", "easter egg", "deep cut",
        "reference", "homage", "spoiler", "meme", "fan theory", "headcanon",
        "retcon", "fanfiction", "fanart", "shipping", "OTP", "power scaling",
        "tier list", "crossover", "expanded universe", "internet culture",
        "english", "spelling", "language", "grammar", "phonology", "phonetics",
        "morphology", "syntax", "semantics", "pragmatics",
    ],

    "Entertainment: Books": [
        "fantasy novels", "science fiction books", "mystery novels",
        "classic literature", "comic books", "bestselling authors",
        "book adaptations", "poetry", "horror fiction", "young adult fiction",
        "non-fiction", "literary awards",
        "magic system", "wizard", "sorcerer", "spellbook", "artifact",
        "ancient relic", "prophecy", "chosen one", "dark lord", "portal fantasy",
        "high fantasy", "dark fantasy", "grimdark",
        "spaceship", "hyperspace", "alien", "first contact", "android",
        "singularity", "time travel", "parallel universe", "alternate timeline",
    ],

    "Entertainment: Film": [
        "blockbuster movies", "film directors", "movie franchises",
        "animated films", "horror movies", "film noir", "documentary films",
        "film scores", "movie trivia", "oscar winners", "cult classics",
        "special effects",
        "spaceship", "starship", "robot", "cyborg", "AI", "time loop",
        "terraforming", "mecha", "dragon", "elf", "dwarf",
        "legendary weapon", "fallen kingdom",
    ],

    "Entertainment: Music": [
        "music", "punk rock", "1990s alternative rock", "1970s rock",
        "classic rock", "grunge music", "EDM", "hip hop", "emo music",
        "2000s pop music", "2000s college rock", "indie rock", "indie music",
        "indie pop", "music news", "music industry", "music recording",
        "the beatles", "1980s music", "2000s music",
    ],

    "Entertainment: Musicals & Theatres": [
        "Broadway", "West End", "musical theatre", "opera",
        "Shakespeare plays", "Tony Awards", "famous playwrights", "ballet",
        "stand-up comedy", "improv", "pantomime", "cabaret",
    ],

    "Entertainment: Television": [
        "sitcoms", "drama series", "reality TV", "talk shows",
        "animated series", "streaming services", "TV pilots", "Emmy Awards",
        "TV reboots", "miniseries",
        "The Simpsons", "The Simpsons characters", "The Simpsons episodes",
        "The Simpsons seasons", "Homer Simpson", "Bart Simpson",
        "Lisa Simpson", "Maggie Simpson", "Marge Simpson", "Moe's Tavern",
        "Krusty Burger", "Krusty the Clown", "Ned Flanders", "Ralph Wiggum",
        "Milhouse Van Houten", "Nelson Muntz",
        "South Park", "Beavis and Butt-Head", "Daria", "Animaniacs",
        "MTV", "VH1", "Snick",
    ],

    "Entertainment: Video Games": [
        "video game", "boss fight", "final boss", "NPC", "open world",
        "sandbox", "level up", "experience points", "skill tree", "perk",
        "loot", "grind", "side quest", "fast travel", "new game plus",
        "Nintendo", "PlayStation", "Xbox", "Steam",
        "minecraft", "fortnite", "league of legends", "valorant", "csgo",
        "dota 2", "overwatch", "wow", "warcraft", "starcraft", "diablo",
        "JRPG", "turn-based combat", "party system", "random encounter",
        "overworld", "summon", "limit break", "status effect",
        "procedural generation", "permadeath", "run-based", "meta progression",
        "metroidvania", "soulslike", "battle royale", "auto battler",
        "idle game", "visual novel", "retro gaming", "arcade",
        "8-bit", "16-bit", "pixel art", "chiptune", "cartridge", "console war",
        "emulation", "romhack", "NES", "SNES",
    ],

    "Entertainment: Board Games": [
        "board game", "deck building", "worker placement", "resource management",
        "area control", "cooperative play", "legacy game", "card game",
        "magic the gathering", "mtg", "pokemon", "yugioh",
        "tabletop", "roleplaying", "campaign", "quest", "dungeon", "encounter",
        "dice", "d20", "critical hit", "critical fail", "initiative",
        "hit points", "character sheet", "character build", "class", "alignment",
        "game master", "homebrew", "one-shot", "minmaxing", "d&d",
        "dungeons and dragons",
    ],

    "Science & Nature": [
        "photosynthesis", "black holes", "DNA", "periodic table", "evolution",
        "climate", "earthquakes", "electricity", "atoms", "planets",
        "ecosystems", "genetics", "human body", "chemistry",
    ],

    "Science: Computers": [
        "programming", "artificial intelligence", "internet history",
        "cybersecurity", "operating systems", "databases", "algorithms",
        "web development", "computer hardware", "software engineering",
        "computer networking", "machine learning",
    ],

    "Science: Mathematics": [
        "prime numbers", "geometry", "calculus", "probability", "statistics",
        "algebra", "fibonacci sequence", "pi", "mathematical proofs",
        "number theory", "fractals", "game theory",
    ],

    "Mythology": [
        "greek mythology", "norse mythology", "egyptian mythology",
        "roman mythology", "japanese mythology", "celtic mythology",
        "hindu mythology", "creation myths", "mythical creatures",
        "legendary heroes", "underworld myths", "trickster gods",
    ],

    "Sports": [
        "sports", "San Diego Padres", "baseball", "MLB", "NBA", "NFL",
        "MLB All-Star Game", "Olympics",
    ],

    "Geography": [
        "continents", "capital cities", "mountains", "rivers", "islands",
        "deserts", "oceans", "national parks", "volcanoes", "rainforests",
        "San Diego", "San Diego", "San Diego", "Southern California", "California",
    ],

    "History": [
        "ancient rome", "world war 2", "renaissance", "cold war",
        "industrial revolution", "silk road", "french revolution",
        "ancient egypt", "viking age", "roman empire", "medieval europe",
        "american civil war", "byzantine empire", "ottoman empire",
    ],

    "Politics": [
        "democracy", "elections", "united nations", "constitution",
        "political parties", "diplomacy", "civil rights movement",
        "propaganda", "monarchy", "revolution", "parliament",
        "political philosophy",
    ],

    "Art": [
        "impressionism", "renaissance art", "modern art", "sculpture",
        "photography", "architecture", "art movements", "famous paintings",
        "street art", "digital art", "art history", "ceramics",
    ],

    "Celebrities": [
        "movie stars", "music legends", "famous athletes",
        "social media influencers", "celebrity scandals", "Hollywood",
        "famous couples", "award shows", "talk shows", "celebrity chefs",
        "fashion icons", "viral moments",
    ],

    "Animals": [
        "dogs", "dog breeds", "cat", "cat breeds", "farm animals",
        "wild animals", "domestic animals", "animals",
    ],

    "Vehicles": [
        "classic cars", "aviation", "trains", "motorcycles", "ships",
        "space vehicles", "electric cars", "race cars", "submarines",
        "military vehicles", "bicycles", "concept cars",
    ],

    "Entertainment: Comics": [
        "Marvel Comics", "DC Comics", "manga", "graphic novels",
        "comic book artists", "superhero origins", "comic conventions",
        "webcomics", "indie comics", "comic book villains",
        "crossover events", "comic book publishers",
    ],

    "Science: Gadgets": [
        "smartphones", "wearable technology", "drones", "virtual reality",
        "3D printing", "smart home", "robotics", "electric vehicles",
        "space technology", "medical devices", "gaming consoles",
        "audio technology",
    ],

    "Entertainment: Japanese Anime & Manga": [
        "shonen anime", "studio ghibli", "anime conventions", "manga artists",
        "mecha anime", "anime music", "light novels", "anime awards",
        "cosplay", "anime history", "magical girl anime", "slice of life anime",
    ],

    "Entertainment: Cartoon & Animations": [
        "1990s", "1990s nostalgia", "Nickelodeon", "Cartoon Network",
        "Pixar", "Disney animation", "Cartoon Network originals",
        "adult animation", "stop motion", "anime influence",
        "animation techniques", "voice acting", "Saturday morning cartoons",
        "cartoon reboots",
    ],
}


# Modifiers to create context and variation
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
    "original intent",

    # Random & unusual
    "random",
    "unusual",
    "odd",
    "rare",
    "unique",
    "obscure",
    "esoteric",
    "uncommon",
    "@#$&(*!)",
    "crazy",
    "weird",
    "strange",
    "unbelievable",
]


def generate_seed(
    *,
    category: Optional[str] = None,
    base_words: Optional[List[str]] = None,
    modifiers: Optional[List[str]] = None,
) -> SeedResult:
    """
    Generate a unique seed by combining a base word with a modifier.

    Args:
        category: Optional OpenTDB category name to pull seeds from.
        base_words: Optional custom list of base words (overrides category lookup).
        modifiers: Optional custom list of modifiers (defaults to MODIFIERS).

    Returns:
        SeedResult: Named tuple with (seed, category).
    """
    mods = modifiers if modifiers is not None else MODIFIERS
    modifier = random.choice(mods)

    if base_words is not None:
        # Custom seeds provided — use them directly
        chosen_category = category if category is not None else "General Knowledge"
        base = random.choice(base_words)
    elif category is not None and category in CATEGORIZED_SEEDS:
        # Category specified — pick from that category's seeds
        chosen_category = category
        base = random.choice(CATEGORIZED_SEEDS[chosen_category])
    else:
        # Neither provided — pick a random category, then a random seed from it
        chosen_category = random.choice(list(CATEGORIZED_SEEDS.keys()))
        base = random.choice(CATEGORIZED_SEEDS[chosen_category])

    return SeedResult(seed=f"{base} :: {modifier}", category=chosen_category)


def get_unused_seed(
    used_seeds: set,
    *,
    category: Optional[str] = None,
    base_words: Optional[List[str]] = None,
    modifiers: Optional[List[str]] = None,
) -> SeedResult:
    """
    Get a seed that hasn't been used yet.

    Tries random selection first (fast path). If most seeds are used,
    enumerates all possibilities to find unused ones. If all seeds exhausted,
    logs a warning and returns a random seed.

    Args:
        used_seeds: Set of previously used seeds.
        category: Optional OpenTDB category name.
        base_words: Optional custom list of base words.
        modifiers: Optional custom list of modifiers (defaults to MODIFIERS).

    Returns:
        SeedResult: An unused seed.
    """
    # Fast path: try 100 random seeds
    for _ in range(100):
        result = generate_seed(category=category, base_words=base_words, modifiers=modifiers)
        if result.seed not in used_seeds:
            return result

    # Slow path: enumerate all possibilities
    mods = modifiers if modifiers is not None else MODIFIERS

    if base_words is not None:
        words = base_words
        chosen_category = category if category is not None else "General Knowledge"
        all_seeds = [
            SeedResult(seed=f"{base} :: {mod}", category=chosen_category)
            for base in words
            for mod in mods
        ]
    elif category is not None and category in CATEGORIZED_SEEDS:
        words = CATEGORIZED_SEEDS[category]
        all_seeds = [
            SeedResult(seed=f"{base} :: {mod}", category=category)
            for base in words
            for mod in mods
        ]
    else:
        all_seeds = [
            SeedResult(seed=f"{base} :: {mod}", category=cat)
            for cat, words in CATEGORIZED_SEEDS.items()
            for base in words
            for mod in mods
        ]

    unused = [r for r in all_seeds if r.seed not in used_seeds]

    if not unused:
        # All seeds exhausted — log warning and return a random seed
        logger.warning("All trivia seeds exhausted. Resetting seed pool.")
        return generate_seed(category=category, base_words=base_words, modifiers=modifiers)

    return random.choice(unused)


def get_total_possible_seeds(
    base_words: Optional[List[str]] = None,
    modifiers: Optional[List[str]] = None,
) -> int:
    """
    Get the total number of unique seeds possible.

    Args:
        base_words: Optional custom list of base words.
        modifiers: Optional custom list of modifiers (defaults to MODIFIERS).

    Returns:
        int: Total combinations.
    """
    mods = modifiers if modifiers is not None else MODIFIERS
    if base_words is not None:
        return len(base_words) * len(mods)
    return sum(len(seeds) for seeds in CATEGORIZED_SEEDS.values()) * len(mods)
