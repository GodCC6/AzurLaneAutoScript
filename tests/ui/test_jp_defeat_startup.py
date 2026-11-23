import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from module.base.button import Button
from module.base.utils import load_image
from module.config import server
from module.config.config_manual import ManualConfig
from module.ui.page import page_campaign
from module.ui.ui import UI


class DefeatReplay:
    has_cached_image = True

    def __init__(self) -> None:
        self.image = load_image(str(Path(__file__).with_name('fixtures') / 'jp_defeat.png'))
        self.clicks = 0

    def stuck_record_add(self, button: Button) -> None:
        pass

    def app_is_running(self) -> bool:
        return True

    def get_orientation(self) -> None:
        pass

    def click(self, button: Button) -> None:
        left, top, right, bottom = button.button
        if not (516 <= left < right <= 740 and 589 <= top < bottom <= 643):
            raise AssertionError(f'Clicked outside the defeat Back button: {button.button}')
        self.clicks += 1

    def screenshot(self) -> None:
        if self.clicks != 1:
            raise AssertionError('JP defeat blocked startup without clicking Back')
        self.image = load_image(page_campaign.check_button.file)


class TestDefeatStartup(unittest.TestCase):
    def test_defeat_back_restores_campaign_page_detection(self) -> None:
        self.addCleanup(server.set_server, server.server)
        server.set_server('jp')
        device = DefeatReplay()
        ui = UI.__new__(UI)
        config = SimpleNamespace(SERVER='jp', BUTTON_OFFSET=ManualConfig.BUTTON_OFFSET,
                                 Emulator_ControlMethod='MaaTouch', Emulator_ScreenshotMethod='ADB_nc')
        ui.interval_timer = {}

        with patch.multiple(ui, create=True, device=device, config=config):
            page = ui.ui_get_current_page()

        self.assertIs(page, page_campaign)
