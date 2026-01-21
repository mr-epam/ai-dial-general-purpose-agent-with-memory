import json
from typing import Any

from task.tools.base import BaseTool
from task.tools.memory.memory_store import LongTermMemoryStore
from task.tools.models import ToolCallParams


class StoreMemoryTool(BaseTool):
    """
    Tool for storing long-term memories about the user.

    The orchestration LLM should extract important, novel facts about the user
    and store them using this tool. Examples:
    - User preferences (likes Python, prefers morning meetings)
    - Personal information (lives in Paris, works at Google)
    - Goals and plans (learning Spanish, traveling to Japan)
    - Important context (has a cat named Mittens)
    """

    def __init__(self, memory_store: LongTermMemoryStore):
        self.memory_store = memory_store

    @property
    def name(self) -> str:
        return "store_memory"

    @property
    def description(self) -> str:
        return (
            "Stores long-term memories about the user for future conversations. "
            "Use this tool when you learn something new, important, or personal about the user that should be remembered. "
            "Examples: preferences (likes Python, prefers morning meetings), personal info (lives in Paris, works at Google), "
            "goals (learning Spanish, traveling to Japan), or important context (has a cat named Mittens). "
            "Only store novel, factual information - avoid storing temporary states or information already in the conversation. "
            "Set higher importance (0.7-1.0) for critical information like preferences, goals, or personal details. "
            "Use lower importance (0.3-0.6) for contextual information or less critical facts."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The memory content to store. Should be a clear, concise fact about the user."
                },
                "category": {
                    "type": "string",
                    "description": "Category of the info (e.g., 'preferences', 'personal_info', 'goals', 'plans', 'context')",
                    "default": "general"
                },
                "importance": {
                    "type": "number",
                    "description": "Importance score between 0 and 1. Higher means more important to remember.",
                    "minimum": 0,
                    "maximum": 1,
                    "default": 0.5
                },
                "topics": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "Related topics or tags for the memory",
                    "default": []
                }
            },
            "required": [
                "content",
                "category"
            ]
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str:
        # 1. Load arguments with `json`
        arguments = json.loads(tool_call_params.tool_call.function.arguments)
        
        # 2. Get `content` from arguments
        content = arguments["content"]
        
        # 3. Get `category` from arguments
        category = arguments.get("category", "general")
        
        # 4. Get `importance` from arguments, default is 0.5
        importance = arguments.get("importance", 0.5)
        
        # 5. Get `topics` from arguments, default is empty array
        topics = arguments.get("topics", [])
        
        # Add request arguments to stage
        stage = tool_call_params.stage
        stage.append_content("## Request arguments: \n")
        stage.append_content(f"**Content**: {content}\n\r")
        stage.append_content(f"**Category**: {category}\n\r")
        stage.append_content(f"**Importance**: {importance}\n\r")
        if topics:
            stage.append_content(f"**Topics**: {', '.join(topics)}\n\r")
        stage.append_content("## Response: \n")
        
        # 6. Call `memory_store` `add_memory`
        result = await self.memory_store.add_memory(
            api_key=tool_call_params.api_key,
            content=content,
            importance=importance,
            category=category,
            topics=topics
        )
        
        # 7. Add result to stage
        stage.append_content(f"{result}\n\r")
        
        # 8. Return result
        return result
