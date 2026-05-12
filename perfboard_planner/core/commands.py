from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List


@dataclass
class Command:
    """Small command object reserved for the next undo/redo pass."""

    label: str
    do: Callable[[], None]
    undo: Callable[[], None]


class CommandStack:
    def __init__(self, limit: int = 100):
        self.limit = limit
        self.undo_stack: List[Command] = []
        self.redo_stack: List[Command] = []

    def execute(self, command: Command) -> None:
        command.do()
        self.undo_stack.append(command)
        if len(self.undo_stack) > self.limit:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def undo(self) -> bool:
        if not self.undo_stack:
            return False
        command = self.undo_stack.pop()
        command.undo()
        self.redo_stack.append(command)
        return True

    def redo(self) -> bool:
        if not self.redo_stack:
            return False
        command = self.redo_stack.pop()
        command.do()
        self.undo_stack.append(command)
        return True
