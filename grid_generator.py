import cv2
import numpy as np
import json

def generate_grid(image_path, cell_size=10, wall_thickness=4):
    """
    Convert blueprint image to a binary occupancy grid.
    0 = free space (traversable)
    1 = wall / obstacle
    """
    print(f"\n🗺️  Generating grid from: {image_path}")

    img = cv2.imread(image_path)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    h, w = gray.shape
    print(f"   Image size: {w}x{h} px")

    # Step 1 — Threshold: dark pixels = walls
    _, binary = cv2.threshold(gray, 150, 255, cv2.THRESH_BINARY_INV)

    # Step 2 — Clean up noise with morphological operations
    kernel = np.ones((2, 2), np.uint8)
    cleaned = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, kernel)

    # Step 3 — Resize to grid
    grid_w = w // cell_size
    grid_h = h // cell_size
    print(f"   Grid size: {grid_w}x{grid_h} cells (cell={cell_size}px)")

    grid = np.zeros((grid_h, grid_w), dtype=np.uint8)

    for row in range(grid_h):
        for col in range(grid_w):
            # Get the pixel block for this cell
            px1 = col * cell_size
            py1 = row * cell_size
            px2 = px1 + cell_size
            py2 = py1 + cell_size
            block = cleaned[py1:py2, px1:px2]
            # If more than 20% of block is dark → mark as wall
            wall_ratio = np.sum(block > 0) / block.size
            grid[row][col] = 1 if wall_ratio > 0.20 else 0

    print(f"   Wall cells: {np.sum(grid == 1)}")
    print(f"   Free cells: {np.sum(grid == 0)}")

    return grid, grid_w, grid_h

def visualise_grid(grid, output_path="grid_overlay.jpg",
                   original_path="test_blueprint.jpg", cell_size=10):
    """
    Draw the grid overlay on top of the original blueprint.
    Red = wall cell, transparent = free cell.
    """
    img = cv2.imread(original_path)
    overlay = img.copy()

    grid_h, grid_w = grid.shape

    for row in range(grid_h):
        for col in range(grid_w):
            if grid[row][col] == 1:
                x1 = col * cell_size
                y1 = row * cell_size
                x2 = x1 + cell_size
                y2 = y1 + cell_size
                cv2.rectangle(overlay, (x1, y1), (x2, y2),
                              (0, 0, 200), -1)  # filled red

    # Blend overlay with original
    result = cv2.addWeighted(overlay, 0.35, img, 0.65, 0)

    # Draw grid lines
    for col in range(grid_w):
        cv2.line(result, (col * cell_size, 0),
                 (col * cell_size, grid_h * cell_size),
                 (200, 200, 200), 1)
    for row in range(grid_h):
        cv2.line(result, (0, row * cell_size),
                 (grid_w * cell_size, row * cell_size),
                 (200, 200, 200), 1)

    cv2.imwrite(output_path, result)
    print(f"   Grid overlay saved → {output_path}")
    return result

def inflate_obstacles(grid, robot_width_cells=2, safety_margin_cells=1):
    """
    Inflate wall cells by robot footprint + safety margin.
    This ensures the robot CENTER never gets too close to a wall.
    """
    inflate_radius = robot_width_cells + safety_margin_cells
    kernel = np.ones((inflate_radius * 2 + 1,
                      inflate_radius * 2 + 1), np.uint8)
    inflated = cv2.dilate(grid.astype(np.uint8), kernel)
    print(f"   Inflated obstacles by {inflate_radius} cells")
    print(f"   Free cells after inflation: {np.sum(inflated == 0)}")
    return inflated

if __name__ == "__main__":
    # Generate grid
    grid, gw, gh = generate_grid(
        "test_blueprint.jpg",
        cell_size=10,       # each cell = 10px of image
        wall_thickness=4
    )

    # Visualise raw grid
    visualise_grid(grid, "grid_raw.jpg", "test_blueprint.jpg", cell_size=10)

    # Inflate for robot safety
    # Assume robot is ~3 cells wide, safety margin 1 cell
    inflated = inflate_obstacles(grid,
                                  robot_width_cells=2,
                                  safety_margin_cells=1)

    # Visualise inflated grid
    visualise_grid(inflated, "grid_inflated.jpg",
                   "test_blueprint.jpg", cell_size=10)

    print("\n✅ Grid generation complete!")
    print(f"   Raw grid shape: {grid.shape}")
    print(f"   Inflated grid shape: {inflated.shape}")
    print("\nNext step: run astar.py to find paths between rooms")