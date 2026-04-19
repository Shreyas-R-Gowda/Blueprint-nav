import cv2
import numpy as np

# ── UNIT CONVERSION ────────────────────────────────────────────────────────
UNIT_TO_CM = {"cm": 1.0, "m": 100.0, "ft": 30.48}


def extract_wall_mask(gray, wall_thickness=4):
    """
    Extract structural walls while suppressing text and symbols.

    Uses directional morphological kernels (horizontal + vertical) to detect
    line-like structures, then filters out small connected components so that
    OCR labels and compass marks do not become wall cells.
    """
    binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY_INV)[1]

    h_kern = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(12, wall_thickness * 6), max(1, wall_thickness))
    )
    v_kern = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(1, wall_thickness), max(12, wall_thickness * 6))
    )
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kern)
    vertical   = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kern)
    walls = cv2.bitwise_or(horizontal, vertical)

    # Reconnect wall corners/joints after removing label strokes.
    join_kern = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (max(3, wall_thickness * 2 + 1), max(3, wall_thickness * 2 + 1))
    )
    walls = cv2.morphologyEx(walls, cv2.MORPH_CLOSE, join_kern)

    # Keep only large structural components — walls form the dominant network.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(walls, connectivity=8)
    filtered = np.zeros_like(walls)
    min_span = max(gray.shape) * 0.12
    min_area = gray.shape[0] * gray.shape[1] * 0.002
    for idx in range(1, count):
        x, y, w, h, area = stats[idx]
        if area >= min_area or w >= min_span or h >= min_span:
            filtered[labels == idx] = 255
    return filtered


def compute_cell_size(image_width_px, building_width_units, unit,
                      robot_width_cm=60.0):
    """
    Derive a grid cell size so each cell ≈ robot_width / 4.
    This means the robot spans ~4 cells, giving enough resolution for
    corridor navigation without an oversized grid.

    Returns:
        cell_size_px     — integer pixels per grid cell
        real_cm_per_cell — real-world centimetres per grid cell
    """
    unit_factor      = UNIT_TO_CM.get(str(unit).lower(), 1.0)
    building_width_cm = building_width_units * unit_factor
    real_cm_per_pixel = building_width_cm / image_width_px
    cell_size_px      = max(2, round(robot_width_cm / 4.0 / real_cm_per_pixel))
    real_cm_per_cell  = cell_size_px * real_cm_per_pixel
    return cell_size_px, real_cm_per_cell


def verify_doorway_clearance(doorway_width_px, robot_width_cm,
                              real_cm_per_pixel, safety_margin_cm=10.0):
    """
    Return (ok, doorway_cm).
    Prints a warning when the doorway is too narrow for the robot + margin.
    """
    doorway_cm   = doorway_width_px * real_cm_per_pixel
    required_cm  = robot_width_cm + safety_margin_cm
    if doorway_cm >= required_cm:
        return True, doorway_cm
    print(f"   ⚠️  Doorway too narrow: {doorway_cm:.1f}cm "
          f"< {required_cm:.1f}cm required (robot {robot_width_cm}cm "
          f"+ {safety_margin_cm}cm margin)")
    return False, doorway_cm


def generate_grid(image_path, cell_size=10, wall_thickness=4,
                  unit="cm", robot_width_cm=60.0, safety_margin_cm=10.0,
                  building_width_units=None):
    """
    Convert a blueprint image to a binary occupancy grid.
      0 = free space (traversable)
      1 = wall / obstacle

    When building_width_units is supplied the cell size is recomputed from
    the real-world scale (via compute_cell_size) so each cell ≈ robot_width/4.
    A guard prevents auto-scaling when it would shrink the grid below 20×20.

    Returns:
        grid             — np.uint8 array (grid_h, grid_w)
        grid_w, grid_h   — column / row count
        real_cm_per_cell — real-world cm represented by one grid cell
    """
    print(f"\n🗺️  Generating grid from: {image_path}")

    img  = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    print(f"   Image size: {w}x{h} px")

    # ── Auto-compute cell size when building scale is known ────────────────
    real_cm_per_cell = float(cell_size)   # fallback: cell_size px ≈ cell_size cm
    scale_applied    = False
    if building_width_units is not None:
        cs_auto, cpc_auto = compute_cell_size(
            w, building_width_units, unit, robot_width_cm
        )
        # Clamp: keep grid ≥ 20 cells and ≤ 200 cells in each dimension.
        # A grid > 200 cells per axis produces too-fine corridors that inflate
        # into dead-ends; min-5px cap keeps the grid under 200×200.
        MAX_GRID_CELLS = 200
        cs_clamped = max(cs_auto, max(w, h) // MAX_GRID_CELLS + 1, 5)
        if cs_clamped != cs_auto:
            _, cpc_auto = compute_cell_size(w, building_width_units * cs_clamped / max(cs_auto, 1),
                                            unit, robot_width_cm)
            # Recompute real_cm_per_cell for the clamped cell size
            unit_factor      = UNIT_TO_CM.get(str(unit).lower(), 1.0)
            building_width_cm = building_width_units * unit_factor
            real_cm_per_pixel = building_width_cm / w
            cpc_auto          = cs_clamped * real_cm_per_pixel
            print(f"   ⚠️  Auto cell size {cs_auto}px → grid too large "
                  f"({w // cs_auto}×{h // cs_auto}) — clamped to {cs_clamped}px")
            cs_auto = cs_clamped
        if w // cs_auto >= 20 and h // cs_auto >= 20:
            cell_size        = cs_auto
            real_cm_per_cell = cpc_auto
            scale_applied    = True
            print(f"   Auto cell size: {cell_size}px  "
                  f"({real_cm_per_cell:.2f} cm/cell)")
        else:
            print(f"   ⚠️  Auto cell size {cs_auto}px → grid too small "
                  f"({w // cs_auto}×{h // cs_auto}) — keeping {cell_size}px")

    # ── Wall extraction (directional morphology + component filtering) ────────
    cleaned = extract_wall_mask(gray, wall_thickness=wall_thickness)

    # ── Build occupancy grid ───────────────────────────────────────────────
    grid_w = w // cell_size
    grid_h = h // cell_size
    print(f"   Grid size: {grid_w}x{grid_h} cells  "
          f"(cell={cell_size}px, {real_cm_per_cell:.1f} cm/cell)")

    grid = np.zeros((grid_h, grid_w), dtype=np.uint8)
    for row in range(grid_h):
        for col in range(grid_w):
            px1, py1 = col * cell_size, row * cell_size
            block     = cleaned[py1:py1 + cell_size, px1:px1 + cell_size]
            if np.sum(block > 0) / block.size > 0.12:
                grid[row][col] = 1

    print(f"   Wall cells: {np.sum(grid == 1)}")
    print(f"   Free cells: {np.sum(grid == 0)}")
    return grid, grid_w, grid_h, real_cm_per_cell, cell_size, scale_applied


def visualise_grid(grid, output_path="grid_overlay.jpg",
                   original_path="test_blueprint.jpg", cell_size=10):
    """Draw grid overlay on the original blueprint. Red = wall cell."""
    img     = cv2.imread(original_path)
    overlay = img.copy()
    gh, gw  = grid.shape
    for row in range(gh):
        for col in range(gw):
            if grid[row][col] == 1:
                x1, y1 = col * cell_size, row * cell_size
                cv2.rectangle(overlay, (x1, y1),
                              (x1 + cell_size, y1 + cell_size),
                              (0, 0, 200), -1)
    result = cv2.addWeighted(overlay, 0.35, img, 0.65, 0)
    for col in range(gw):
        cv2.line(result, (col * cell_size, 0),
                 (col * cell_size, gh * cell_size), (200, 200, 200), 1)
    for row in range(gh):
        cv2.line(result, (0, row * cell_size),
                 (gw * cell_size, row * cell_size), (200, 200, 200), 1)
    cv2.imwrite(output_path, result)
    print(f"   Grid overlay saved → {output_path}")
    return result


def inflate_obstacles(grid, robot_width_cells=2, safety_margin_cells=1):
    """
    Inflate wall cells by robot half-width + safety margin (in grid cells).

    inflate_radius = robot_width_cells + safety_margin_cells
    """
    inflate_radius = robot_width_cells + safety_margin_cells
    kernel  = np.ones((inflate_radius * 2 + 1, inflate_radius * 2 + 1), np.uint8)
    inflated = cv2.dilate(grid.astype(np.uint8), kernel)
    print(f"   Inflated obstacles by {inflate_radius} cells "
          f"(½-width={robot_width_cells} + margin={safety_margin_cells})")
    print(f"   Free cells after inflation: {np.sum(inflated == 0)}")
    return inflated


if __name__ == "__main__":
    grid, gw, gh, real_cm_per_cell, cell_size_px, _ = generate_grid(
        "test_blueprint.jpg",
        cell_size=10,
        wall_thickness=4,
    )
    print(f"   real_cm_per_cell: {real_cm_per_cell:.1f} cm  cell_size: {cell_size_px}px")

    visualise_grid(grid, "grid_raw.jpg", "test_blueprint.jpg", cell_size=cell_size_px)

    import math
    inflated = inflate_obstacles(grid,
                                  robot_width_cells=max(1, math.ceil(30.0 / real_cm_per_cell)),
                                  safety_margin_cells=max(1, math.ceil(10.0 / real_cm_per_cell)))

    visualise_grid(inflated, "grid_inflated.jpg",
                   "test_blueprint.jpg", cell_size=cell_size_px)

    print("\n✅ Grid generation complete!")
    print(f"   Raw grid shape:      {grid.shape}")
    print(f"   Inflated grid shape: {inflated.shape}")
    print("\nNext step: run astar.py to find paths between rooms")
