import numpy as np

from module.base.button import Button
from module.base.decorator import cached_property
from module.base.timer import Timer
from module.exception import GameStuckError, MapDetectionError
from module.logger import logger
from module.map.camera import Camera
from module.map.map_base import location2node, location_ensure
from module.map_detection.os_grid import OSGrid
from module.map_detection.view import View
from module.os.map_operation import OSMapOperation
from module.os.radar import Radar


class OSCamera(OSMapOperation, Camera):
    radar: Radar
    fleet_current: tuple

    def _map_swipe(self, vector, box=(239, 128, 993, 628)):
        return super()._map_swipe(vector, box=box)

    def _view_init(self):
        if not hasattr(self, 'view'):
            storage = ((10, 7), [(110.307, 103.657), (1012.311, 103.657), (-32.959, 600.567), (1113.057, 600.567)])
            view = View(self.config, mode='os', grid_class=OSGrid)
            view.detector_set_backend('homography')
            view.backend.load_homography(storage=storage)
            self.view = view

    @cached_property
    def radar(self):
        """
        Returns:
            Radar:
        """
        return Radar(self.config)

    def predict_radar(self):
        """
        Scan radar and merge it into map
        """
        self.radar.predict(self.device.image)
        self.radar.show()

    def grid_is_in_sight(self, grid, camera=None, sight=None):
        location = location_ensure(grid)
        camera = location_ensure(camera) if camera is not None else self.camera
        if sight is None:
            sight = self.map.camera_sight

        diff = np.array(location) - camera
        if diff[1] > sight[3]:
            y = diff[1] - sight[3]
        elif diff[1] < sight[1]:
            y = diff[1] - sight[1]
        else:
            y = 0
        if diff[0] > sight[2]:
            x = diff[0] - sight[2]
        elif diff[0] < sight[0]:
            x = diff[0] - sight[0]
        else:
            x = 0
        return x == 0 and y == 0

    # def ensure_edge_insight(self, reverse=False, preset=None, swipe_limit=(4, 3)):
    #     return super().ensure_edge_insight(reverse=reverse, preset=preset, swipe_limit=swipe_limit)
    #
    # def focus_to(self, location, swipe_limit=(4, 3)):
    #     return super().focus_to(location, swipe_limit=swipe_limit)

    def _get_map_outside_button(self):
        """
        Returns:
            Button: Click outside of map.
        """
        for _ in range(2):
            if self.view.left_edge:
                edge = self.view.backend.left_edge
                area = (113, 185, edge.get_x(290), 290)
            elif self.view.right_edge:
                edge = self.view.backend.right_edge
                area = (edge.get_x(360), 360, 1280, 560)
            else:
                logger.info('No left edge or right edge')
                self.ensure_edge_insight()
                continue

            button = Button(area=area, color=(), button=area, name='MAP_OUTSIDE')
            return button

    def update_os(self):
        """
        Similar to `Camera.update()`, but for OPSI.
        """
        # self.device.screenshot()
        self._view_init()

        try:
            self.view.load(self.device.image)
        except (MapDetectionError, AttributeError) as e:
            logger.warning(e)
            logger.warning('Assuming camera is focused on grid center')

            def empty(*args, **kwargs):
                pass

            backup, self.view.backend.load = self.view.backend.load, empty
            self.view.backend.homo_loca = (53, 60)
            self.view.backend.left_edge = False
            self.view.backend.right_edge = False
            self.view.backend.lower_edge = False
            self.view.backend.upper_edge = False
            self.view.load(self.device.image)
            self.view.backend.load = backup

    def convert_radar_to_local(self, location):
        """
        Converts the coordinate on radar to the coordinate of local map view,
        also handles a rare game bug.

        Usually, OPSI camera focus on current fleet, which is (5, 4) in local view.
        The convert should be `local = view[np.add(radar, view.center_loca)]`
        However, Azur Lane may bugged, not focusing current.
        In this case, the convert should base on fleet position.

        If target grid is beyond a map edge detected in local view, the edge is a misdetection,
        because radar shows that the grid exists.
        This happens when the commander dialog popup after auto search is still on screen,
        its bottom border is detected as map edge. Re-detect local view until the popup is gone.

        Args:
            location: (x, y), Position on radar.

        Returns:
            OSGrid: Grid instance in self.view

        Raises:
            KeyError: If target grid not in local view.
            GameStuckError: If target grid stays beyond a misdetected map edge.
        """
        location = location_ensure(location)

        timeout = Timer(10, count=10).start()
        while 1:
            try:
                return self._convert_radar_to_local(location)
            except KeyError as e:
                # e.args[0] is the grid location that is not in local view
                if not self._is_beyond_view_edge(e.args[0]):
                    raise
                if timeout.reached():
                    raise GameStuckError('Radar target grid stays beyond a misdetected map edge')
                logger.warning(f'Radar target grid {e.args[0]} is beyond a map edge in local view, '
                               f'map edge is misdetected, re-detect local view')

            self.device.screenshot()
            self.update_os()
            self.view.predict()

    def _is_beyond_view_edge(self, local):
        """
        Args:
            local: (x, y), Grid location not in local view.

        Returns:
            bool: If a map edge detected in local view lies between local view and the grid.
        """
        x, y = local
        backend = self.view.backend
        return bool(
            (x < 0 and backend.left_edge)
            or (x > self.view.shape[0] and backend.right_edge)
            or (y < 0 and backend.lower_edge)
            or (y > self.view.shape[1] and backend.upper_edge)
        )

    def _convert_radar_to_local(self, location):
        """
        Args:
            location: (x, y), Position on radar.

        Returns:
            OSGrid: Grid instance in self.view

        Raises:
            KeyError: If target grid not in local view.
        """
        fleets = self.view.select(is_current_fleet=True)
        if fleets.count == 1:
            center = fleets[0].location
        elif fleets.count > 1:
            logger.warning(f'Convert radar to local, but found multiple current fleets: {fleets}')
            fleets = fleets.sort_by_camera_distance(self.view.center_loca)
            center = fleets[0].location
            logger.warning(
                f'Assuming the nearest fleet to camera canter is current fleet: {location2node(center)}')
        else:
            logger.warning(f'Convert radar to local, but current fleet not found. '
                           f'Assuming camera center is current fleet: {location2node(self.view.center_loca)}')
            center = self.view.center_loca

        try:
            local = self.view[np.add(location, center)]
        except KeyError:
            logger.warning(f'Convert radar to local, but target grid not in local view. '
                           f'Assuming camera center is current fleet: {location2node(self.view.center_loca)}')
            center = self.view.center_loca
            local = self.view[np.add(location, center)]

        logger.info('Radar %s -> Local %s (fleet=%s)' % (
            str(location),
            location2node(local.location),
            location2node(center)
        ))
        return local
