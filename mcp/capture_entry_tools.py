#!/usr/bin/env python3
from typing import Any, Dict


def build_handlers(*, capture_game_fn):
    def _capture_game(args: Dict[str, Any]) -> Dict[str, Any]:
        return capture_game_fn(args)

    return {"capture_game": _capture_game}

