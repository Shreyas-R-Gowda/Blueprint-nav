import numpy as np

from backend.app.services.planner import astar, build_directions_and_commands, smooth_path


def test_astar_finds_path():
    grid = np.array([[0, 0, 0], [1, 1, 0], [0, 0, 0]], dtype=np.uint8)
    path = astar(grid, (0, 0), (2, 2))
    assert path[0] == (0, 0)
    assert path[-1] == (2, 2)


def test_smoothing_keeps_endpoints():
    path = [(0, 0), (0, 1), (0, 2), (1, 2)]
    smoothed = smooth_path(path)
    assert smoothed[0] == (0, 0)
    assert smoothed[-1] == (1, 2)


def test_direction_generation_shape():
    directions, commands = build_directions_and_commands([(0, 0), (0, 1), (0, 2)], "N", 10.0, 1.0)
    assert directions
    assert commands
