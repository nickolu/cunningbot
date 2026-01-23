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
BASE_WORDS = [
    # People - Historical Figures
    "einstein", "shakespeare", "napoleon", "cleopatra", "davinci", "newton", "gandhi",
    "lincoln", "churchill", "beethoven", "mozart", "picasso", "michelangelo",
    "columbus", "washington", "jefferson", "roosevelt", "mandela", "kingmlk",
    "curie", "darwin", "galileo", "aristotle", "plato", "socrates", "confucius",
    "caesar", "alexander", "genghiskhan", "charlemagne", "victoria", "elizabeth",

    # People - Modern Figures
    "jobs", "gates", "bezos", "musk", "zuckerberg", "winfrey", "disney",
    "spielberg", "hitchcock", "kubrick", "tarantino", "scorsese",

    # Places - Landmarks
    "pyramids", "colosseum", "eiffeltower", "tajmahal", "greatwall", "statueofliberty",
    "bigben", "operahouse", "christredeemer", "machupicchu", "petra", "angkorwat",
    "stonehenge", "acropolis", "forbiddencity", "kremlin", "whitehouse", "louvre",

    # Places - Geographic Features
    "everest", "amazon", "sahara", "nile", "mississippi", "rockies", "andes",
    "himalayas", "alps", "kilimanjaro", "grandcanyon", "niagara", "victoria",
    "mariana", "pacific", "atlantic", "mediterranean", "caribbean",

    # Places - Cities/Regions
    "rome", "athens", "paris", "london", "newyork", "tokyo", "beijing",
    "moscow", "istanbul", "jerusalem", "mecca", "cairo", "baghdad", "venice",

    # Things - Inventions
    "wheel", "telephone", "internet", "computer", "airplane", "automobile",
    "television", "radio", "lightbulb", "penicillin", "printing", "compass",
    "telescope", "microscope", "steam", "electricity", "nuclear", "laser",
    "transistor", "vaccine", "antibiotics", "photography", "cinema",

    # Things - Concepts
    "democracy", "capitalism", "communism", "renaissance", "enlightenment",
    "revolution", "evolution", "relativity", "quantum", "gravity", "dna",
    "atom", "molecule", "photosynthesis", "magnetism", "radiation",

    # Events - Wars & Conflicts
    "worldwar1", "worldwar2", "civilwar", "coldwar", "vietnam", "korea",
    "napoleonic", "trojan", "crusades", "revolution", "independence",

    # Events - Historical Periods
    "renaissance", "medieval", "ancient", "industrial", "victorian",
    "roaring20s", "great depression", "prohibition", "goldrush",

    # Sports & Games
    "olympics", "worldcup", "superbowl", "baseball", "basketball", "football",
    "soccer", "tennis", "golf", "boxing", "marathon", "chess",

    # Arts & Literature
    "hamlet", "odyssey", "illiad", "bible", "quran", "moby", "gatsby",
    "pride", "dracula", "sherlock", "1984", "fahrenheit", "hobbit",
    "starwars", "startrek", "marvel", "dc", "disney", "pixar",

    # Science & Nature
    "blackhole", "bigbang", "galaxy", "planet", "star", "comet", "asteroid",
    "volcano", "earthquake", "tsunami", "hurricane", "tornado", "lightning",
    "dinosaur", "mammoth", "neanderthal", "fossil", "extinction",
]

# Modifiers to create context and variation (50+ modifiers)
MODIFIERS = [
    # Time-based
    "origin", "beginning", "end", "peak", "discovery", "invention", "birth",
    "death", "childhood", "youth", "decline", "renaissance", "golden_age",
    "founding", "creation", "destruction", "collapse", "rise", "fall",

    # Context-based
    "impact", "significance", "legacy", "controversy", "achievement", "failure",
    "triumph", "tragedy", "mystery", "scandal", "revolution", "transformation",

    # Attributes
    "first", "last", "largest", "smallest", "fastest", "slowest", "oldest",
    "newest", "highest", "lowest", "longest", "shortest", "strongest", "weakest",

    # Relations
    "successor", "predecessor", "rival", "ally", "creator", "inventor",
    "founder", "leader", "champion", "pioneer", "master", "apprentice",

    # Other aspects
    "theory", "principle", "law", "formula", "equation", "method", "technique",
    "style", "movement", "school", "era", "period", "age", "epoch",
    "capital", "center", "hub", "birthplace", "homeland", "territory",
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
