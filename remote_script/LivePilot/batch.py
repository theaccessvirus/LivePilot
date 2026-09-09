"""
LivePilot - batch command.

Runs up to BATCH_MAX bounded, synchronous sub-commands in one main-thread
hop. The MCP side pays one socket round trip and one settle delay instead
of one per command, which is what makes composite actions (a track, a
clip, its notes, a name) fast enough to feel like one operation.

Contract:
- at most BATCH_MAX sub-commands
- every sub-command type must pass is_batchable(); nothing runs until all
  of them do
- results are indexed by position
- execution stops at the first failure; the reply carries the partial
  results, the failing index, and that error under "failure"
- no automatic replay: the caller decides what to do with a partial batch
"""

from . import router
from .utils import error_response, success_response, INVALID_PARAM

BATCH_MAX = 50

# Prefixes whose handlers finish synchronously inside one LOM call and stay
# bounded in size. Loading, freezing, flattening, exporting, capturing, and
# browser walks are excluded by omission.
BATCHABLE_PREFIXES = (
    "create_",
    "add_",
    "set_",
    "get_",
    "remove_",
    "duplicate_",
    "modify_",
    "transpose_",
)

# Registered handlers that match a prefix above but walk unbounded trees or
# wait on Live.
BATCH_DENIED = frozenset([
    "batch",
    "get_batchable_commands",
    "get_browser_items",
    "get_browser_tree",
    "get_session_diagnostics",
])


def is_batchable(command_type):
    """True if *command_type* may run inside a batch."""
    if not isinstance(command_type, str) or command_type in BATCH_DENIED:
        return False
    if command_type not in router._handlers:
        return False
    return command_type.startswith(BATCHABLE_PREFIXES)


def batchable_commands():
    return sorted(c for c in router._handlers if is_batchable(c))


def _validate(commands):
    """Return an error message or None. Nothing has executed yet."""
    if not isinstance(commands, list):
        return "'commands' must be a list"
    if not commands:
        return "'commands' is empty"
    if len(commands) > BATCH_MAX:
        return "batch holds %d commands, limit is %d" % (len(commands), BATCH_MAX)
    for index, item in enumerate(commands):
        if not isinstance(item, dict):
            return "command %d is not an object" % index
        cmd_type = item.get("type")
        if not isinstance(cmd_type, str) or not cmd_type:
            return "command %d has no 'type'" % index
        if cmd_type not in router._handlers:
            return "command %d: unknown command type %s" % (index, cmd_type)
        if not is_batchable(cmd_type):
            return "command %d: %s is not batchable" % (index, cmd_type)
        params = item.get("params", {})
        if params is not None and not isinstance(params, dict):
            return "command %d: 'params' must be an object" % index
    return None


@router.register("batch")
def batch(song, params):
    commands = params.get("commands")
    problem = _validate(commands)
    if problem:
        raise ValueError(problem)

    results = []
    for index, item in enumerate(commands):
        sub = {
            "id": "batch-%d" % index,
            "type": item["type"],
            "params": item.get("params") or {},
        }
        reply = router.dispatch(song, sub)
        if reply.get("ok"):
            results.append({"index": index, "type": sub["type"], "ok": True,
                            "result": reply.get("result")})
            continue
        results.append({"index": index, "type": sub["type"], "ok": False,
                        "error": reply.get("error")})
        # Named "failure", not "error": the router treats a top-level
        # "error" key as a handler failure and would drop the partial results.
        return {
            "completed": index,
            "total": len(commands),
            "failed_index": index,
            "failure": reply.get("error"),
            "results": results,
            "stopped": True,
        }
    return {
        "completed": len(commands),
        "total": len(commands),
        "failed_index": None,
        "results": results,
        "stopped": False,
    }


@router.register("get_batchable_commands")
def get_batchable_commands(song, params):
    return {"max": BATCH_MAX, "commands": batchable_commands()}
