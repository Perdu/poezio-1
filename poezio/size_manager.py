"""
Size Manager:
    used to check size boundaries of the whole window and
    specific tabs
"""
THRESHOLD_WIDTH_DEGRADE = 45
THRESHOLD_HEIGHT_DEGRADE = 10

FULL_WIDTH_DEGRADE = 66
FULL_HEIGHT_DEGRADE = 10

class SizeManager(object):

    def __init__(self, core, win_cls):
        self._win_class = win_cls
        self._core = core

    @property
    def tab_degrade_x(self):
        _, x = self._win_class._tab_win.getmaxyx()
        return x < THRESHOLD_WIDTH_DEGRADE

    @property
    def tab_degrade_y(self):
        y, x = self._win_class._tab_win.getmaxyx()
        return y < THRESHOLD_HEIGHT_DEGRADE

    @property
    def core_degrade_x(self):
        y, x = self._core.stdscr.getmaxyx()
        return x < FULL_WIDTH_DEGRADE

    @property
    def core_degrade_y(self):
        y, x = self._core.stdscr.getmaxyx()
        return y < FULL_HEIGHT_DEGRADE

