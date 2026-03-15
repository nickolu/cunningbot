"""Shared constants for the trivia game.

This module contains category color mappings, a default fallback color,
and a legacy category map for backward compatibility with stored game data.
"""

CATEGORY_COLORS = {
    # History/Politics: brown tones
    "General Knowledge": 0xCD853F,  # peru
    "History": 0x8B4513,  # brown
    "Politics": 0xA0522D,  # sienna

    # Science & Nature/Computers/Mathematics/Gadgets: blue tones
    "Science & Nature": 0x4169E1,  # royal blue
    "Science: Computers": 0x1E90FF,  # dodger blue
    "Science: Mathematics": 0x00BFFF,  # deep sky blue
    "Science: Gadgets": 0x87CEEB,  # sky blue

    # Entertainment subcategories (Film, TV, Music, etc.): pink/magenta tones
    "Entertainment: Books": 0xFF69B4,  # hot pink
    "Entertainment: Film": 0xFF1493,  # deep pink
    "Entertainment: Music": 0xFF1493,  # deep pink
    "Entertainment: Musicals & Theatres": 0xDB7093,  # pale violet red
    "Entertainment: Television": 0xFFB6C1,  # light pink
    "Entertainment: Comics": 0xC71585,  # medium violet red

    # Video Games/Board Games: purple tones
    "Entertainment: Video Games": 0x8A2BE2,  # blue violet
    "Entertainment: Board Games": 0x9932CC,  # dark orchid

    # Japanese Anime & Manga / Cartoon & Animations: pink/magenta tones
    "Entertainment: Japanese Anime & Manga": 0xFF1493,  # deep pink
    "Entertainment: Cartoon & Animations": 0xE05263,  # dusty rose

    # Sports: orange-red
    "Sports": 0xFF4500,  # orange-red

    # Art/Mythology: purple tones
    "Art": 0x9370DB,  # medium purple
    "Mythology": 0x7B68EE,  # medium slate blue

    # Geography: green
    "Geography": 0x228B22,  # forest green

    # Celebrities: gold
    "Celebrities": 0xFFD700,  # gold

    # Animals: teal
    "Animals": 0x008080,  # teal

    # Vehicles: steel/gray
    "Vehicles": 0x708090,  # slate gray
}

DEFAULT_CATEGORY_COLOR = 0x0099FF

LEGACY_CATEGORY_MAP = {
    "History": "History",
    "Science": "Science & Nature",
    "Sports": "Sports",
    "Entertainment": "Entertainment: Film",
    "Arts & Literature": "Art",
    "Geography": "Geography",
}
