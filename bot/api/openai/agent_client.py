from typing import List

from agents import Agent, Runner, RunResult, Tool


class AgentClient:
    def __init__(self, name: str, instructions: str, tools: List[Tool]) -> None:
        self.name = name
        self.instructions = instructions
        self.agent = Agent(name=name, instructions=instructions, tools=tools)

    async def run(self, prompt: str) -> str:
        result = await Runner.run(self.agent, prompt)
        return str(result.final_output)