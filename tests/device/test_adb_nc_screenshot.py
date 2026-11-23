from __future__ import annotations

import io
import socket
import struct
from types import SimpleNamespace

import pytest
from adbutils.errors import AdbError, AdbTimeout
from PIL import Image

from module.device.method.utils import RETRY_TRIES
from module.device.screenshot import Screenshot
from module.exception import RequestHumanTakeover

RGB = (173, 91, 47)
SIZE = (720, 1280)


class Stream:
    def __init__(self, data: bytes = b'', *, timeout: bool = False) -> None:
        self.data = data
        self.timeout = timeout
        self.closed = False

    def settimeout(self, seconds: float) -> None:
        pass

    def recv(self, size: int) -> bytes:
        if self.timeout:
            raise socket.timeout()
        data, self.data = self.data, b''
        return data

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()


class Listener(Stream):
    def __init__(self, accepted: Stream | None) -> None:
        super().__init__()
        self.accepted = accepted

    def accept(self):
        if self.accepted is None:
            raise socket.timeout()
        return self.accepted, ('127.0.0.1', 0)


class Replay(Screenshot):
    def __init__(self, scenario: str, direct: str = 'valid', *, bluestacks: bool = True, http: bool = False) -> None:
        self.__dict__['config'] = SimpleNamespace(
            DEVICE_OVER_HTTP=http, Error_SaveError=False, Emulator_ScreenshotDedithering=False,
            Emulator_ScreenshotMethod='ADB_nc',
        )
        self.serial = 'emulator-5554'
        self._screenshot_interval = SimpleNamespace(wait=lambda: None, reset=lambda: None)
        self.__dict__['_nc_server_host_port'] = ('127.0.0.1', 12345, '10.0.2.2', 12345)
        self.__dict__['is_bluestacks_air'] = bluestacks
        self.__dict__['nc_command'] = ['nc']
        self.__dict__['adb'] = SimpleNamespace(shell=self.shell)
        self.__dict__['u2'] = SimpleNamespace(shell=self.http_shell)
        self.scenario = scenario
        self.direct = direct
        self.events: list[str] = []
        self.nc_streams: list[Stream] = []
        self.direct_streams: list[Stream] = []
        self.listeners: list[Listener] = []
        self.__dict__['reverse_server'] = self.new_listener()

    def new_listener(self) -> Listener:
        height, width = SIZE
        data = struct.pack('=III', width, height, 1) + bytes((*RGB, 255)) * (width * height)
        accepted = None if self.scenario.startswith('accept') or self.scenario == 'opening' else Stream(
            data, timeout=self.scenario == 'read',
        )
        listener = Listener(accepted)
        self.listeners.append(listener)
        return listener

    def shell(self, command: list[str], **kwargs) -> Stream:
        if '-p' in command:
            self.events.append('direct')
            assert all(stream.closed for stream in self.nc_streams)
            if 'nc' in self.events:
                assert all(listener.accepted is None or listener.accepted.closed for listener in self.listeners)
            if self.direct == 'offline':
                raise AdbError('device offline')
            data = b''
            if self.direct == 'valid':
                with io.BytesIO() as encoded:
                    pixels = bytes(RGB) * (SIZE[0] * SIZE[1])
                    Image.frombytes('RGB', SIZE[::-1], pixels).save(encoded, format='PNG')
                    data = encoded.getvalue()
            result = Stream(data)
            self.direct_streams.append(result)
            return result

        self.events.append('nc')
        if self.scenario == 'offline':
            raise AdbError('device offline')
        if self.scenario == 'opening':
            raise AdbTimeout('adb read timeout')
        result = Stream(timeout=self.scenario == 'accept')
        self.nc_streams.append(result)
        return result

    def http_shell(self, command: list[str], **kwargs):
        self.events.append('nc')
        return SimpleNamespace(content=b'')

    def adb_reconnect(self) -> None:
        self.events.append('reconnect')
        self.__dict__['reverse_server'] = self.new_listener()


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr('module.device.method.adb.time.sleep', lambda seconds: None)


@pytest.mark.parametrize('scenario', ['read', 'accept', 'opening'])
def test_nc_read_timeout_uses_one_direct_capture_before_reconnect(scenario: str) -> None:
    device = Replay(scenario)

    image = device.screenshot()

    assert tuple(image[0, 0]) == RGB
    assert device.events == ['nc', 'direct']
    assert device.direct_streams[0].closed
    assert device.listeners[0].closed


@pytest.mark.parametrize('direct', ['offline', 'empty'])
def test_failed_direct_capture_keeps_nc_reconnect_budget(direct: str) -> None:
    device = Replay('read', direct)

    with pytest.raises(RequestHumanTakeover):
        device.screenshot()

    assert device.events == ['nc', 'direct'] + ['reconnect', 'nc'] * (RETRY_TRIES - 1)
    assert all(stream.closed for stream in device.nc_streams + device.direct_streams)


def test_normal_nc_capture_returns_frame_and_reuses_listener() -> None:
    device = Replay('success')

    image = device.screenshot()

    assert tuple(image[0, 0]) == RGB
    assert device.events == ['nc']
    assert device.nc_streams[0].closed
    accepted = device.listeners[0].accepted
    assert accepted is not None and accepted.closed
    assert not device.listeners[0].closed


@pytest.mark.parametrize('scenario, bluestacks, http', [
    ('offline', True, False),
    ('accept_without_read_timeout', True, False),
    ('read', False, False),
    ('read', True, True),
])
def test_offline_and_other_devices_keep_existing_reconnect_behavior(scenario: str, bluestacks: bool,
                                                                    http: bool) -> None:
    device = Replay(scenario, bluestacks=bluestacks, http=http)

    with pytest.raises(RequestHumanTakeover):
        device.screenshot()

    assert device.events == ['nc'] + ['reconnect', 'nc'] * (RETRY_TRIES - 1)


def test_native_adb_method_still_returns_png_frame() -> None:
    device = Replay('success')
    device.config.Emulator_ScreenshotMethod = 'ADB'

    image = device.screenshot()

    assert tuple(image[0, 0]) == RGB
    assert device.events == ['direct']
