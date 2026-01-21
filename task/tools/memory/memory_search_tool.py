import json
from typing import Any

from task.tools.base import BaseTool
from task.tools.memory._models import MemoryData
from task.tools.memory.memory_store import LongTermMemoryStore
from task.tools.models import ToolCallParams


class SearchMemoryTool(BaseTool):
    """
    Tool for searching long-term memories about the user.

    Performs semantic search over stored memories to find relevant information.
    """

    def __init__(self, memory_store: LongTermMemoryStore):
        self.memory_store = memory_store


    @property
    def name(self) -> str:
        return "search_memory"

    @property
    def description(self) -> str:
        return (
            "Searches long-term memories about the user using semantic similarity. "
            "Use this tool when you need to recall previously stored information about the user, their preferences, "
            "personal details, goals, or context. The search uses semantic matching, so you can search with natural "
            "language queries or keywords. Examples: 'What programming languages does the user like?', 'user preferences', "
            "'travel plans', 'personal information'. Returns the most relevant memories ranked by similarity. "
            "If no memories match, returns an empty result. Use top_k to control how many results to return (1-20, default 5)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query. Can be a question or keywords to find relevant memories"
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of most relevant memories to return.",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 5
                }
            },
            "required": [
                "query"
            ]
        }

    async def _execute(self, tool_call_params: ToolCallParams) -> str:
        # 1. Load arguments with `json`
        arguments = json.loads(tool_call_params.tool_call.function.arguments)
        
        # 2. Get `query` from arguments
        query = arguments["query"]
        
        # 3. Get `top_k` from arguments, default is 5
        top_k = arguments.get("top_k", 5)
        
        # Add request arguments to stage
        stage = tool_call_params.stage
        stage.append_content("## Request arguments: \n")
        stage.append_content(f"**Query**: {query}\n\r")
        stage.append_content(f"**Top K**: {top_k}\n\r")
        stage.append_content("## Response: \n")
        
        # 4. Call `memory_store` `search_memories`
        results = await self.memory_store.search_memories(
            api_key=tool_call_params.api_key,
            query=query,
            top_k=top_k
        )
        
        # 5. If results are empty then set `final_result` as "No memories found.",
        #    otherwise iterate through results and collect content, category and topics (if preset) in markdown format
        if not results:
            final_result = "No memories found."
        else:
            markdown_lines = []
            for i, memory_data in enumerate(results, 1):
                markdown_lines.append(f"### Memory {i}")
                markdown_lines.append(f"**Content**: {memory_data.content}")
                markdown_lines.append(f"**Category**: {memory_data.category}")
                if memory_data.topics:
                    markdown_lines.append(f"**Topics**: {', '.join(memory_data.topics)}")
                markdown_lines.append("")  # Empty line between memories
            
            final_result = "\n".join(markdown_lines)
        
        # 6. Add result to stage as markdown text
        stage.append_content(f"{final_result}\n\r")
        
        # 7. Return result
        return final_result
