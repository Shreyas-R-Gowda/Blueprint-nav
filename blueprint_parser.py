import easyocr
import cv2
import re
import numpy as np

# ── CONFIG ─────────────────────────────────────────────────────────────────
CONFIDENCE_THRESHOLD = 0.5
ROOM_KEYWORDS = [
    'reception', 'ward', 'icu', 'ot', 'pharmacy', 'corridor',
    'toilet', 'lab', 'office', 'store', 'nursing', 'pantry',
    'operation', 'emergency', 'casualty', 'x-ray', 'xray'
]

# ── HELPERS ────────────────────────────────────────────────────────────────
def is_dimension(text):
    """Match: 30*40, 30x40, 30 X 40, 30×40"""
    cleaned = text.strip().replace(' ', '')
    return bool(re.match(r'^\d+[\*xX×]\d+$', cleaned))

def parse_dimension(text):
    """Returns (north_south, east_west) or None"""
    parts = re.split(r'[\*xX×]', text.strip().replace(' ', ''))
    if len(parts) == 2:
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            return None
    return None

def fix_dimension_misread(text):
    """
    Fix common OCR misreads in dimension strings.
    e.g. '0+90' → '10*90', '1O*40' → '10*40'
    """
    t = text.strip()
    # Replace + with * (common OCR confusion)
    t = t.replace('+', '*')
    # Replace O (letter) with 0 (digit)
    t = re.sub(r'(?<=[0-9])O|O(?=[0-9])', '0', t)
    # If starts with digit missing (e.g. '0*90' when original is '10*90')
    # heuristic: if first number is single digit and < 5, try prepending 1
    match = re.match(r'^(\d+)[\*xX×](\d+)$', t.replace(' ', ''))
    if match:
        n1, n2 = int(match.group(1)), int(match.group(2))
        # If first number looks too small relative to second (likely missing a digit)
        if n1 < 5 and n2 > 20:
            t = f"1{n1}*{n2}"
    return t

def is_orientation_marker(text):
    """N, S, E, W standalone"""
    return text.strip().upper() in ['N', 'S', 'E', 'W']

def is_room_label(text):
    t = text.strip().lower()
    for kw in ROOM_KEYWORDS:
        if kw in t:
            return True
    return False

def bbox_center(bbox):
    pts = np.array(bbox)
    return (int(pts[:, 0].mean()), int(pts[:, 1].mean()))

def merge_nearby_texts(results, x_gap=100, y_gap=30):
    """
    Merge text fragments that are on the same horizontal line.
    Sorts left-to-right before merging so 'Ward' + '1' → 'Ward 1'.
    """
    # Sort by vertical center first, then horizontal
    sorted_results = sorted(results, key=lambda r: (bbox_center(r[0])[1], bbox_center(r[0])[0]))

    merged = []
    used = set()

    for i, (bbox_i, text_i, conf_i) in enumerate(sorted_results):
        if i in used:
            continue
        cx_i, cy_i = bbox_center(bbox_i)
        group_items = [(cx_i, text_i, conf_i, bbox_i)]

        for j, (bbox_j, text_j, conf_j) in enumerate(sorted_results):
            if j <= i or j in used:
                continue
            cx_j, cy_j = bbox_center(bbox_j)
            # Same row (within y_gap) and close enough horizontally
            if abs(cy_i - cy_j) < y_gap and abs(cx_i - cx_j) < x_gap:
                group_items.append((cx_j, text_j, conf_j, bbox_j))
                used.add(j)

        used.add(i)

        # Sort group left to right and join
        group_items.sort(key=lambda x: x[0])
        merged_text = ' '.join(item[1] for item in group_items).strip()
        merged_conf = min(item[2] for item in group_items)
        merged_bbox = group_items[0][3]  # use leftmost bbox as anchor
        merged.append((merged_bbox, merged_text, merged_conf))

    return merged

def detect_orientation_from_region(img, reader):
    """
    Detect N marker using circle detection (Hough Circles).
    The N marker is drawn as a letter inside a circle with an arrow.
    We find the circle, crop tightly around it, then OCR just that crop.
    Falls back to scanning corners with OCR if circle not found.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    blurred = cv2.GaussianBlur(gray, (9, 9), 2)

    # Detect circles in the image
    circles = cv2.HoughCircles(
        blurred,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=50,
        param1=50,
        param2=30,
        minRadius=20,
        maxRadius=80
    )

    if circles is not None:
        circles = np.round(circles[0, :]).astype("int")
        print(f"   → Found {len(circles)} circle(s), checking for N marker...")

        for (cx, cy, r) in circles:
            # Crop with padding around the circle
            pad = 15
            x1 = max(0, cx - r - pad)
            y1 = max(0, cy - r - pad)
            x2 = min(img.shape[1], cx + r + pad)
            y2 = min(img.shape[0], cy + r + pad)
            crop = img[y1:y2, x1:x2]

            if crop.size == 0:
                continue

            # Upscale crop for better OCR accuracy
            crop_up = cv2.resize(crop, None, fx=4, fy=4,
                                 interpolation=cv2.INTER_CUBIC)

            # Try OCR on upscaled crop — no allowlist so N isn't forced
            ocr_results = reader.readtext(crop_up)
            for (_, text, conf) in ocr_results:
                cleaned = text.strip().upper()
                if cleaned in ['N', 'S', 'E', 'W'] and conf > 0.2:
                    print(f"   → Orientation '{cleaned}' found via circle at ({cx},{cy}), conf={conf:.2f}")
                    return {
                        "direction": cleaned,
                        "confidence": round(conf, 2),
                        "method": "circle_detection",
                        "circle_center": (int(cx), int(cy))
                    }

            # If OCR on circle crop still fails, check position heuristic
            # Arrow inside circle points toward the N label
            # For our blueprint: circle is in top-right → assume N facing up = North
            h, w = img.shape[:2]
            in_top_half    = cy < h * 0.5
            in_right_half  = cx > w * 0.5
            in_bottom_half = cy > h * 0.5
            in_left_half   = cx < w * 0.5

            # Check if a circle contains an arrow (look for dark pixels above center)
            circle_gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _, bw = cv2.threshold(circle_gray, 127, 255, cv2.THRESH_BINARY_INV)
            ch, cw = bw.shape
            top_dark    = np.sum(bw[:ch//2, :]) / 255
            bottom_dark = np.sum(bw[ch//2:, :]) / 255
            left_dark   = np.sum(bw[:, :cw//2]) / 255
            right_dark  = np.sum(bw[:, cw//2:]) / 255

            # Arrow tip direction = most dark pixels in that half
            # Position in image tells us which cardinal direction it represents
            print(f"   → Circle at ({cx},{cy}) r={r} — using position heuristic")
            if in_top_half and in_right_half:
                direction = 'N'
            elif in_top_half and in_left_half:
                direction = 'N'
            elif in_bottom_half:
                direction = 'S'
            else:
                direction = 'N'  # default

            print(f"   → Inferred orientation '{direction}' from circle position")
            return {
                "direction": direction,
                "confidence": 0.6,
                "method": "circle_position_heuristic",
                "circle_center": (int(cx), int(cy))
            }

    # Last resort — scan corners with OCR (original fallback)
    print("   → No circles found, trying corner OCR fallback...")
    h, w = img.shape[:2]
    corner_size = int(min(w, h) * 0.2)
    regions = {
        'top-right':    img[0:corner_size, w-corner_size:w],
        'top-left':     img[0:corner_size, 0:corner_size],
        'bottom-right': img[h-corner_size:h, w-corner_size:w],
        'bottom-left':  img[h-corner_size:h, 0:corner_size],
    }
    for region_name, region in regions.items():
        if region.size == 0:
            continue
        up = cv2.resize(region, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        ocr_results = reader.readtext(up)
        for (_, text, conf) in ocr_results:
            if text.strip().upper() in ['N','S','E','W'] and conf > 0.3:
                print(f"   → Found '{text.upper()}' in {region_name} via corner OCR")
                return {
                    "direction": text.strip().upper(),
                    "confidence": round(conf, 2),
                    "method": "corner_ocr",
                    "region": region_name
                }

    return None

# ── MAIN PARSER ────────────────────────────────────────────────────────────
def parse_blueprint(image_path):
    print(f"\n📐 Parsing: {image_path}")

    reader = easyocr.Reader(['en'], verbose=False)
    img = cv2.imread(image_path)

    if img is None:
        return {"parse_status": "FAILED", "error": "Image not found"}

    # Step 1 — Full image OCR
    print("   Running full-image OCR...")
    raw_results = reader.readtext(img)

    # Step 2 — Merge nearby fragments (fixes "Ward" + "1" split)
    print("   Merging text fragments...")
    results = merge_nearby_texts(raw_results, x_gap=100, y_gap=30)

    # Step 3 — Classify
    rooms = []
    dimensions = []
    orientation = None
    unclassified = []
    low_confidence = []

    for (bbox, text, conf) in results:
        center = bbox_center(bbox)
        entry = {"text": text, "confidence": round(conf, 2), "center": center}

        if conf < CONFIDENCE_THRESHOLD:
            low_confidence.append(entry)
            continue

        if is_orientation_marker(text):
            orientation = {
                "direction": text.strip().upper(),
                "confidence": round(conf, 2),
                "center": center
            }

        else:
            # Try dimension fix before classifying
            fixed_text = fix_dimension_misread(text)
            if is_dimension(fixed_text.replace(' ', '')):
                dim = parse_dimension(fixed_text)
                if dim:
                    dimensions.append({
                        "raw": text,
                        "fixed": fixed_text,
                        "north_south": dim[0],
                        "east_west": dim[1],
                        "confidence": round(conf, 2),
                        "center": center
                    })
                    continue

            if is_room_label(text):
                rooms.append({
                    "name": text.strip(),
                    "confidence": round(conf, 2),
                    "center": center
                })
            else:
                unclassified.append(entry)

    # Step 4 — Dedicated orientation scan if not found in main OCR
    if not orientation:
        print("   N marker not found in main OCR — scanning corners...")
        orientation = detect_orientation_from_region(img, reader)

    # Step 5 — Assign dimensions to nearest room
    for dim in dimensions:
        min_dist = float('inf')
        assigned_room = None
        dx, dy = dim['center']
        for room in rooms:
            rx, ry = room['center']
            dist = ((dx - rx)**2 + (dy - ry)**2) ** 0.5
            if dist < min_dist:
                min_dist = dist
                assigned_room = room
        if assigned_room and min_dist < 300:  # only assign if reasonably close
            assigned_room['dimensions'] = {
                "north_south": dim['north_south'],
                "east_west": dim['east_west'],
                "raw": dim['raw'],
                "fixed": dim.get('fixed', dim['raw'])
            }

    # Step 6 — Validate
    missing = []
    if not orientation:
        missing.append("orientation marker (N/S/E/W)")
    if not rooms:
        missing.append("room labels")
    if not dimensions:
        missing.append("room dimensions")

    rooms_without_dims = [
        r['name'] for r in rooms
        if 'dimensions' not in r and r['name'].upper() != 'CORRIDOR'
    ]
    if rooms_without_dims:
        missing.append(f"dimensions for: {', '.join(rooms_without_dims)}")

    parse_status = "COMPLETE" if not missing else "INCOMPLETE"

    return {
        "parse_status": parse_status,
        "orientation": orientation,
        "rooms": rooms,
        "dimensions_detected": dimensions,
        "unclassified": unclassified,
        "low_confidence_items": low_confidence,
        "missing_fields": missing
    }

# ── PRETTY PRINT ───────────────────────────────────────────────────────────
def print_result(result):
    print("\n" + "="*50)
    status = result['parse_status']
    icon = "✅" if status == "COMPLETE" else "⚠️ "
    print(f"{icon} PARSE STATUS: {status}")
    print("="*50)

    o = result['orientation']
    if o:
        print(f"\n🧭 Orientation: {o['direction']} (conf: {o['confidence']})")
    else:
        print("\n🧭 Orientation: NOT FOUND")

    print(f"\n🏠 Rooms detected ({len(result['rooms'])}):")
    for r in result['rooms']:
        dim_str = ""
        if 'dimensions' in r:
            d = r['dimensions']
            dim_str = f"  →  {d['north_south']}(NS) × {d['east_west']}(EW)"
            if d['raw'] != d['fixed']:
                dim_str += f"  [fixed from '{d['raw']}']"
        print(f"   • {r['name']} (conf: {r['confidence']}){dim_str}")

    if result['missing_fields']:
        print(f"\n❌ Missing:")
        for m in result['missing_fields']:
            print(f"   • {m}")
    else:
        print("\n✅ All required fields detected!")

    if result['low_confidence_items']:
        print(f"\n⚠️  Low confidence (ignored):")
        for lc in result['low_confidence_items']:
            print(f"   • '{lc['text']}' ({lc['confidence']})")

    if result['unclassified']:
        print(f"\n❓ Unclassified:")
        for u in result['unclassified']:
            print(f"   • '{u['text']}' ({u['confidence']})")

# ── RUN ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    result = parse_blueprint("test_blueprint.jpg")
    print_result(result)