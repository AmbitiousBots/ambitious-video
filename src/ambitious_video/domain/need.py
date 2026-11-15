from __future__ import annotations


class NeedTool(Exception):
    def __init__(self, tool: str) -> None:
        super().__init__(tool)
        self.tool = tool
