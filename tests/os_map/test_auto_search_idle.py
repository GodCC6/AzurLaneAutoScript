"""Tests for the idle guard in OSMap.os_auto_search_daemon.

Background: the daemon cleared the stuck watchdog on every iteration while the OS map was on screen, and
nothing else bounded the loop. If the game sat on the map without starting a combat and without showing any
button the daemon knows, Alas took a screenshot every 0.3s for hours, without a click, a log line or an error
(observed 2026-10-03: 2h44m of silence). The guard raises GameStuckError after OS_AUTO_SEARCH_IDLE_LIMIT
seconds in the map without any progress, so the normal restart/save-error path takes over.

The daemon is run unbound against a small fake `self` and a fake clock, so hours of simulated time take
milliseconds. The real Timer, the real daemon code and the real exceptions are used.
"""
from unittest.mock import patch

import pytest

from module.exception import CampaignEnd, GameStuckError
from module.logger import logger
from module.os.map import OS_AUTO_SEARCH_IDLE_LIMIT, OSMap

TICK = 0.3  # simulated seconds per loop iteration, same as the screenshot interval
MAX_ITERATIONS = 200_000  # simulated 16 hours; a daemon still running here is a livelock

PROGRESS_SOURCES = ['map_option', 'retirement', 'combat', 'map_event']


class _Device:
    def stuck_record_clear(self):
        pass


class _Config:
    def task_switched(self):
        return False


class FakeOSMap:
    """Just enough of OSMap for os_auto_search_daemon to run, with the game state scripted by the test."""

    def __init__(self, clock, in_map=True, progress_via=None, progress_every=None, end_after=None):
        self.clock = clock
        self.in_map = in_map
        self.progress_via = progress_via
        self.progress_every = progress_every
        self.end_after = end_after
        self.started_at = clock['t']
        self.last_progress = clock['t']
        self.iterations = 0
        self.progress_count = 0
        self.need_repair = [False] * 6
        self.device = _Device()
        self.config = _Config()

    # The loop that screenshots; here it only advances the fake clock.
    def loop(self):
        while True:
            self.iterations += 1
            if self.iterations > MAX_ITERATIONS:
                raise AssertionError(
                    f'daemon still looping after {self.clock["t"] - self.started_at:.0f}s of simulated time')
            self.clock['t'] += TICK
            yield None

    def _progress(self, source):
        if source != self.progress_via:
            return False
        if self.clock['t'] - self.last_progress >= self.progress_every:
            self.last_progress = self.clock['t']
            self.progress_count += 1
            return True
        return False

    def is_in_map(self):
        return self.in_map

    def appear(self, *args, **kwargs):
        return True  # unlock check passes at once

    def handle_os_auto_search_map_option(self, drop=None, enable=True):
        if self.end_after is not None and self.clock['t'] - self.started_at >= self.end_after:
            raise CampaignEnd
        return self._progress('map_option')

    def handle_retirement(self):
        return self._progress('retirement')

    def combat_appear(self):
        return self._progress('combat')

    def handle_map_event(self):
        return self._progress('map_event')

    def auto_search_combat(self, drop=None):
        return True

    def on_auto_search_battle_count_reset(self):
        pass

    def on_auto_search_battle_count_add(self):
        pass

    def hp_reset(self):
        pass

    def hp_get(self):
        pass


def _run(fake, clock):
    """Run the real daemon against `fake`, with the Timer clock and logging replaced."""
    with patch('module.base.timer.time', lambda: clock['t']), \
            patch.object(logger, 'hr'), patch.object(logger, 'warning'), patch.object(logger, 'info'):
        return OSMap.os_auto_search_daemon(fake, drop=None)


def _new_clock():
    return {'t': 1_000_000.0}


class TestIdleGuard:
    def test_stalled_in_map_raises_game_stuck_at_the_limit(self):
        clock = _new_clock()
        fake = FakeOSMap(clock, in_map=True)

        with pytest.raises(GameStuckError):
            _run(fake, clock)

        elapsed = clock['t'] - fake.started_at
        assert OS_AUTO_SEARCH_IDLE_LIMIT <= elapsed < OS_AUTO_SEARCH_IDLE_LIMIT + 5

    @pytest.mark.parametrize('source', PROGRESS_SOURCES)
    def test_progress_resets_the_idle_timer(self, source):
        # Progress every 9 minutes for 36 minutes: far longer than the limit in total, never longer than the limit
        # between two progress events. Any one source failing to reset the timer makes this raise.
        clock = _new_clock()
        every = OS_AUTO_SEARCH_IDLE_LIMIT * 0.9
        fake = FakeOSMap(clock, in_map=True, progress_via=source, progress_every=every,
                         end_after=every * 4 + 1)

        with pytest.raises(CampaignEnd):
            _run(fake, clock)

        assert fake.progress_count >= 3  # the scripted progress really reached the daemon

    def test_limit_is_not_tighter_than_a_normal_gap_between_combats(self):
        # Combats normally follow each other within a minute or two. A limit near that would restart healthy runs.
        assert OS_AUTO_SEARCH_IDLE_LIMIT >= 300

    def test_outside_the_map_the_guard_is_not_responsible(self):
        # Not in the map, the regular stuck watchdog owns the situation; the guard must not fire on its own.
        clock = _new_clock()
        fake = FakeOSMap(clock, in_map=False, end_after=OS_AUTO_SEARCH_IDLE_LIMIT * 3)

        with pytest.raises(CampaignEnd):
            _run(fake, clock)
