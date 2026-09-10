"""
LivePilot - Undo history read (1 command).

`get_undo_state` reports whether Live has anything to undo or redo, without
touching either. Tools that must prove they leave the undo history alone
(per-track measurement, anti-clash) read this before and after their work.
"""

from .router import register


@register("get_undo_state")
def get_undo_state(song, params):
    """Return Song.can_undo and Song.can_redo. Read-only."""
    return {"can_undo": bool(song.can_undo), "can_redo": bool(song.can_redo)}
