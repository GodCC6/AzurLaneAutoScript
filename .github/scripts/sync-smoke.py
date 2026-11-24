"""Exercise the checked-out OS search paths without emulator or image dependencies."""
import ast
from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

clock = 1000
env = {"logger": Mock(), "show_function_call": Mock()}
for suffix in ("ON", "OFF", "OFF_DISABLED"):
    env[f"AUTO_SEARCH_OS_MAP_OPTION_{suffix}"] = suffix
env["AUTO_SEARCH_REWARD"] = "REWARD"


def select(path, names):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    found = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    found += [node for node in tree.body if isinstance(node, ast.Assign) and
              any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    assert len(found) == len(names), (path, names)
    exec(compile(ast.Module(body=found, type_ignores=[]), path, "exec"), env)


for path in ("module/base/timer.py", "module/exception.py"):
    exec(compile(Path(path).read_text(encoding="utf-8"), path, "exec"), env)
env["time"] = lambda: clock
select("module/device/device.py", {"stuck_record_check", "stuck_record_clear"})
select("module/os_handler/map_event.py", {"handle_os_auto_search_map_option"})
select("module/os/map.py", {"os_auto_search_daemon", "OS_AUTO_SEARCH_IDLE_LIMIT"})


def watch(state, moving=False, transition=None, frames=900):
    global clock
    clock = 1000
    dev = SimpleNamespace(
        detect_record=set(), stuck_long_wait_list=[], app_is_running=lambda: True, click=Mock(),
        stuck_timer=env["Timer"](60, count=60).start(),
        stuck_timer_long=env["Timer"](180, count=180).start(),
    )
    dev.stuck_record_clear = partial(env["stuck_record_clear"], dev)

    def loop():
        global clock
        for _ in range(frames):
            clock += 1
            env["stuck_record_check"](dev)
            yield

    def hit(kind):
        return kind == transition and clock == 1601

    main = SimpleNamespace(
        device=dev, loop=loop, is_in_map=lambda: True,
        # Replay a map button that remains visible but cannot be acted on by the handler.
        match_template_color=lambda asset, interval=0, **kw: asset == state and not interval,
        appear=lambda asset, **kw: asset == state,
        walk_counter_changed=lambda reset=False: not reset and moving and clock % 4 == 0,
        hp_reset=Mock(), on_auto_search_battle_count_reset=Mock(),
        on_auto_search_battle_count_add=Mock(), info_bar_count=lambda: 0, interval_reset=Mock(),
        handle_retirement=lambda: hit("retirement"), combat_appear=lambda: hit("combat"),
        auto_search_combat=lambda **kw: True, handle_map_event=lambda: hit("map_event"),
    )
    real_option = partial(env["handle_os_auto_search_map_option"], main)
    main.handle_os_auto_search_map_option = lambda **kw: hit("option") or real_option(**kw)
    try:
        return env["os_auto_search_daemon"](main), clock - 1000
    except env["GameStuckError"] as exc:
        return exc, clock - 1000


outcome, elapsed = watch("ON")
assert isinstance(outcome, env["GameStuckError"]) and elapsed < 600, (outcome, elapsed)
assert watch("ON", moving=True) == (0, 900)
for state in ("OFF", "OFF_DISABLED"):
    outcome, elapsed = watch(state)
    assert isinstance(outcome, env["GameStuckError"]) and 600 <= elapsed < 605, (state, outcome, elapsed)
for transition in ("option", "retirement", "combat", "map_event"):
    assert watch("OFF", transition=transition, frames=1201) == (int(transition == "combat"), 1201)
print("8 OS search replay scenarios passed")
