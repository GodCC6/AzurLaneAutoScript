"""Replay checked-out recovery paths without emulator or image dependencies."""
import ast
import collections
from functools import partial
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

clock = 1000
env = {"logger": Mock(), "show_function_call": Mock(), "collections": collections}
for suffix in ("ON", "OFF", "OFF_DISABLED"):
    env[f"AUTO_SEARCH_OS_MAP_OPTION_{suffix}"] = suffix
for name in (
    "AUTO_SEARCH_REWARD", "STORY_FINISHED", "REWARD_GOT", "STORY_FIRST",
    "STORY_LAST", "STORY_MIDDLE", "BATTLE_MIDDLE", "SWITCH_20241219_STORY",
):
    env[name] = name


def select(path, names, class_name):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    cls, = (node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    found = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert len(found) == len(names), (path, names)
    scope = dict(env)
    exec(compile(ast.Module(body=found, type_ignores=[]), path, "exec"), scope)
    return SimpleNamespace(**{name: scope[name] for name in names})


for path in ("module/base/timer.py", "module/exception.py"):
    exec(compile(Path(path).read_text(encoding="utf-8"), path, "exec"), env)
env["time"] = lambda: clock
device = select("module/device/device.py", {
    "stuck_record_check", "stuck_record_clear", "click_record_add", "click_record_check",
    "click_record_clear", "handle_control_check",
}, "Device")
option = select("module/os_handler/map_event.py", {"handle_os_auto_search_map_option"}, "MapEventHandler")
os_map = select("module/os/map.py", {"os_auto_search_daemon"}, "OSMap")


def watch(state, moving=False, clickable=False, frames=900):
    global clock
    clock = 1000
    dev = SimpleNamespace(
        detect_record=set(), stuck_long_wait_list=[], app_is_running=lambda: True,
        stuck_timer=env["Timer"](60, count=60).start(),
        stuck_timer_long=env["Timer"](180, count=180).start(),
        click_record=collections.deque(maxlen=15),
    )
    for name, method in vars(device).items():
        setattr(dev, name, partial(method, dev))
    dev.click = dev.handle_control_check

    def loop():
        global clock
        for _ in range(frames):
            clock += 1
            dev.stuck_record_check()
            yield

    def match(asset, interval=0, **kwargs):
        return asset == state and (not interval or clickable and (clock - 1001) % interval == 0)

    main = SimpleNamespace(
        device=dev, loop=loop, is_in_map=lambda: True,
        match_template_color=match, appear=lambda asset, **kw: asset == state,
        get_walk_counter=lambda: (clock // 4) % 4 if moving else 1,
        hp_reset=Mock(), on_auto_search_battle_count_reset=Mock(),
        on_auto_search_battle_count_add=Mock(), info_bar_count=lambda: 0, interval_reset=Mock(),
        handle_retirement=lambda: False, combat_appear=lambda: False, handle_map_event=lambda: False,
    )
    main.handle_os_auto_search_map_option = partial(option.handle_os_auto_search_map_option, main)
    try:
        return os_map.os_auto_search_daemon(main), clock - 1000
    except (env["GameStuckError"], env["GameTooManyClickError"]) as exc:
        return exc, clock - 1000


for state in ("ON", "OFF", "OFF_DISABLED"):
    outcome, elapsed = watch(state)
    assert isinstance(outcome, env["GameStuckError"]) and 60 <= elapsed < 75, (state, outcome, elapsed)
assert watch("ON", moving=True) == (0, 900)
for state in ("OFF", "OFF_DISABLED"):
    outcome, elapsed = watch(state, clickable=True)
    assert isinstance(outcome, env["GameTooManyClickError"]) and elapsed <= 36, (state, outcome, elapsed)
print("6 OS search replay scenarios passed")

story = select("module/eventstory/eventstory_base.py", {"event_story", "run_event_story"}, "EventStoryBase")


def play(frames):
    global clock
    clock = 1000
    current = next(frames)

    def screenshot():
        nonlocal current
        global clock
        clock += 1
        current = next(frames)

    main = SimpleNamespace(
        device=SimpleNamespace(screenshot=screenshot, click_record_clear=Mock()),
        is_combat_executing=lambda: current == "battle", is_combat_loading=lambda: False,
        match_template_color=lambda asset, **kw: asset == "STORY_FINISHED" and current == "finished",
        appear=lambda asset, **kw: asset == "SWITCH_20241219_STORY" and current in ("stalled", "click"),
        appear_then_click=lambda asset, **kw: asset == "STORY_FIRST" and current == "click",
        handle_story_skip=lambda: False, handle_get_items=lambda: False,
        handle_event_story=lambda: False, story_skip_interval_clear=Mock(), popup_interval_clear=Mock(),
    )
    return story.event_story(main), main


assert play(iter(["stalled"] * 12))[0] == "reset"
outcome, story_main = play(iter(["stalled"] * 10 + ["click"] + ["stalled"] * 10 + ["finished"]))
assert outcome == "finish" and story_main.device.click_record_clear.call_count == 1
assert play(iter(["other"] * 15 + ["battle"]))[0] == "battle"


def sequence(results, allow_kill=False):
    main = SimpleNamespace(
        ui_goto_event_story=Mock(side_effect=["story"] * len(results) + ["finish"]),
        event_story=Mock(side_effect=results), app_stop=Mock(), app_start=Mock(),
        allow_event_battle_kill_game=allow_kill, combat_execute=Mock(), combat_expected_end=Mock(),
        config=SimpleNamespace(override=Mock()),
    )
    story.run_event_story(main)
    return main


assert sequence(["reset", "reset"]).app_start.call_count == 0
assert sequence(["reset", "reset", "reset"]).app_start.call_count == 1
story_main = sequence(["reset", "reset", "battle", "reset", "reset"])
assert story_main.app_start.call_count == 0
story_main.combat_execute.assert_called_once_with(expected_end=story_main.combat_expected_end)
story_main = sequence(["battle"], allow_kill=True)
assert story_main.app_start.call_count == 1 and story_main.combat_execute.call_count == 0

dispatch = select("module/eventstory/eventstory.py", {"run"}, "EventStory")
normal, veritas = Mock(), Mock()
dispatch.run.__globals__["EventStoryBase"] = normal
module_name = "module.eventstory.eventstory_20261124"
module = ModuleType(module_name)
module.EventStory20261124 = veritas
sys.modules[module_name] = module
story_main = SimpleNamespace(config=SimpleNamespace(cross_get=lambda _: ""), device=object())
dispatch.run(story_main)
normal.assert_called_once_with(config=story_main.config, device=story_main.device)
normal.return_value.run.assert_called_once()
veritas.assert_not_called()
story_main.config.cross_get = lambda _: "coalition_20261124"
dispatch.run(story_main)
veritas.assert_called_once_with(config=story_main.config, device=story_main.device)
veritas.return_value.run.assert_called_once()
assert normal.call_count == 1
print("9 event-story replay scenarios passed")
