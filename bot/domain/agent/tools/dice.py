"""The `roll_dice` agent tool."""

from typing import Any, Dict

from bot.app.commands.dice.roll import DiceRoller
from bot.app.utils.logger import get_logger
from bot.domain.agent.tools.base import AgentTool

logger = get_logger()


SCHEMA: dict = {
    "type": "function",
    "function": {
        "name": "roll_dice",
        "description": (
            "Roll dice using standard notation (e.g. '2d6', '1d20+5', '4d6+2d4*10'). "
            "Returns the breakdown and total."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "Dice expression like '2d6', '1d20+3', '4d6'",
                },
            },
            "required": ["expression"],
        },
    },
}


async def execute_roll_dice(arguments: Dict[str, Any]) -> str:
    """Execute the roll_dice tool."""
    expression = arguments.get("expression", "1d20")
    roller = DiceRoller()
    try:
        breakdown, total, original = roller.parse_and_roll(expression)
        return f"Rolled {original}: {breakdown} = **{total}**"
    except ValueError as e:
        return f"Dice error: {e}"


TOOL = AgentTool(
    config_key="dice",
    schema=SCHEMA,
    executor=execute_roll_dice,
    channel_aware=False,
)
