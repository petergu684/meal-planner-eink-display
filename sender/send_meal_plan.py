#!/usr/bin/env python3
"""
Meal Plan E-Ink Display Sender
Fetches meal plan from meal-planner and sends to ELECROW CrowPanel 5.79" E-Paper

Display: 792x272, 1-bit black & white
Format: raw 1-bit bitmap, MSB first, 1=white 0=black

Usage:
    python send_meal_plan.py                    # Auto-detect display IP, send current week
    python send_meal_plan.py --ip 192.168.1.100 # Specify display IP
    python send_meal_plan.py --week 2025-04-21  # Specific week (Monday date)
    python send_meal_plan.py --preview          # Just generate image, don't send
"""

import argparse
import sqlite3
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import json
import urllib.request
import urllib.error
import socket

# PIL for image generation
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Installing Pillow...")
    os.system("pip install Pillow -q")
    from PIL import Image, ImageDraw, ImageFont

# ============== CONFIG ==============
# Path to a SQLite database that stores meal plans.
# Expected schema (adapt get_meal_plan() if yours differs):
#   meal_plan(id, week_start TEXT 'YYYY-MM-DD', day_of_week INTEGER 0-6, meal_type TEXT 'lunch'|'dinner', dish_id INTEGER)
#   dish(id, name TEXT)
# Override with the MEAL_PLANNER_DB environment variable.
MEAL_PLANNER_DB = os.environ.get(
    "MEAL_PLANNER_DB",
    "/mnt/ssd1/llm/meal-planner/data/meal_planner.db"
)
# ELECROW CrowPanel 5.79" E-Paper: 792x272, 1-bit B&W
DISPLAY_WIDTH = 792
DISPLAY_HEIGHT = 272
DISPLAY_PORT = 80

# Colors (B&W)
WHITE = 255
BLACK = 0

# Chinese days
DAYS_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


def get_week_start(date: Optional[datetime] = None) -> datetime:
    """Get Monday of the current week."""
    if date is None:
        date = datetime.now()
    days_since_monday = date.weekday()
    monday = date - timedelta(days=days_since_monday)
    return monday.replace(hour=0, minute=0, second=0, microsecond=0)


def get_meal_plan(week_start: datetime) -> dict:
    """Fetch meal plan from SQLite database."""
    if not os.path.exists(MEAL_PLANNER_DB):
        raise FileNotFoundError(f"Database not found: {MEAL_PLANNER_DB}")

    conn = sqlite3.connect(MEAL_PLANNER_DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT mp.id, mp.week_start, mp.day_of_week, mp.meal_type,
               d.name as dish_name, d.id as dish_id
        FROM meal_plan mp
        JOIN dish d ON mp.dish_id = d.id
        WHERE mp.week_start = ?
        ORDER BY mp.day_of_week, mp.meal_type
    """, (week_start.strftime("%Y-%m-%d"),))

    plan = {}
    for row in cursor.fetchall():
        day_offset = row['day_of_week']
        actual_date = week_start + timedelta(days=day_offset)
        date_str = actual_date.strftime("%Y-%m-%d")
        meal_type = row['meal_type']
        dish_name = row['dish_name']

        if date_str not in plan:
            plan[date_str] = {'lunch': [], 'dinner': []}
        plan[date_str][meal_type].append(dish_name)

    conn.close()
    return plan


def load_fonts():
    """Load fonts with CJK support fallback."""
    # Try CJK fonts first (for Chinese characters)
    cjk_font_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/truetype/arphic/uming.ttc",
    ]
    cjk_bold_paths = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/google-noto-cjk/NotoSansCJKsc-Bold.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]

    # DejaVu as absolute fallback (no CJK support)
    latin_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    latin_bold_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]

    def find_font(paths, size):
        for p in paths:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    # Prefer CJK fonts, fall back to Latin
    title_font = find_font(cjk_bold_paths + latin_bold_paths, 22)
    day_font = find_font(cjk_bold_paths + latin_bold_paths, 16)
    meal_font = find_font(cjk_font_paths + latin_paths, 12)
    dish_font_large = find_font(cjk_font_paths + latin_paths, 20)  # <=4 chars
    dish_font_small = find_font(cjk_font_paths + latin_paths, 15)  # >4 chars
    small_font = find_font(cjk_font_paths + latin_paths, 10)

    return title_font, day_font, meal_font, dish_font_large, dish_font_small, small_font


def _draw_centered_dishes(draw, dishes, cell_x, cell_y, cell_w, cell_h,
                          font_large, font_small, is_today=False):
    """Draw dish names centered horizontally and vertically within a cell.

    - Show up to 4 dishes; if more exist, show "..." at the bottom.
    - Dishes with <=4 chars use font_large (20px).
    - Dishes with >4 chars use font_small (15px); truncate to 6 chars + "...".
    - If is_today, draw black background with white text.
    """
    fill_color = 1 if is_today else 0  # 1=white text on black, 0=black text on white
    show_dishes = dishes[:4]
    has_more = len(dishes) > 4

    if not show_dishes:
        show_dishes = ["-"]
        has_more = False

    # Prepare lines: (display_text, font)
    items = []
    for dish in show_dishes:
        if len(dish) > 6:
            text = dish[:6] + "..."
            font = font_small
        elif len(dish) > 4:
            text = dish
            font = font_small
        else:
            text = dish
            font = font_large
        items.append((text, font))

    # Measure each line
    line_bboxes = []
    for text, font in items:
        bb = font.getbbox(text)
        line_bboxes.append(bb)

    # If has_more, add "……" as an extra line (CJK ellipsis renders at proper size)
    ellipsis_bb = None
    ellipsis_font = font_large
    ellipsis_text = "……"
    if has_more:
        ellipsis_bb = ellipsis_font.getbbox(ellipsis_text)

    line_spacing = 3
    vis_heights = [bb[3] - bb[1] for bb in line_bboxes]
    total_h = sum(vis_heights) + line_spacing * (len(items) - 1)
    if ellipsis_bb:
        total_h += line_spacing + (ellipsis_bb[3] - ellipsis_bb[1])

    start_y = cell_y + (cell_h - total_h) // 2

    for i, (text, font) in enumerate(items):
        bb = line_bboxes[i]
        lx = cell_x + (cell_w - (bb[2] - bb[0])) // 2
        ly = start_y - bb[1]
        draw.text((lx, ly), text, font=font, fill=fill_color)
        start_y += vis_heights[i] + line_spacing

    if ellipsis_bb:
        ew = ellipsis_bb[2] - ellipsis_bb[0]
        lx = cell_x + (cell_w - ew) // 2
        ly = start_y - ellipsis_bb[1]
        draw.text((lx, ly), ellipsis_text, font=ellipsis_font, fill=fill_color)



def create_meal_plan_image(week_start: datetime, plan: dict) -> Image.Image:
    """Generate a 792x272 B&W meal plan image for e-ink display."""

    img = Image.new('1', (DISPLAY_WIDTH, DISPLAY_HEIGHT), 1)  # 1-bit, white background
    draw = ImageDraw.Draw(img)

    title_font, day_font, meal_font, dish_font_large, dish_font_small, small_font = load_fonts()

    # Title bar
    week_end = week_start + timedelta(days=6)
    week_str = f"{week_start.month}/{week_start.day} - {week_end.month}/{week_end.day}"
    title = f"Meal Plan  {week_str}"

    title_h = 30
    draw.text((DISPLAY_WIDTH // 2, title_h // 2), title,
              font=title_font, fill=0, anchor="mm")
    # Timestamp in top-right corner
    updated = f"Updated: {datetime.now().strftime('%m/%d %H:%M')}"
    draw.text((DISPLAY_WIDTH - 6, title_h // 2), updated,
              font=small_font, fill=0, anchor="rm")
    # Separator line under title
    draw.line([0, title_h, DISPLAY_WIDTH, title_h], fill=0, width=1)

    # Grid layout: 7 columns
    margin = 4
    top_y = title_h + 2
    col_w = (DISPLAY_WIDTH - margin * 2) // 7
    content_h = DISPLAY_HEIGHT - top_y - 2
    row_h = content_h // 2  # Lunch and dinner rows

    for day_idx in range(7):
        x = margin + day_idx * col_w
        day_date = week_start + timedelta(days=day_idx)
        date_str = day_date.strftime("%Y-%m-%d")
        is_today = day_date.date() == datetime.now().date()

        # Day header
        header_h = 20
        day_label = DAYS_CN[day_idx] + f" {day_date.day}"
        if is_today:
            draw.rectangle([x, top_y, x + col_w - 2, top_y + header_h], fill=0, outline=1, width=1)
            draw.text((x + col_w // 2, top_y + header_h // 2), day_label,
                      font=day_font, fill=1, anchor="mm")
        else:
            draw.rectangle([x, top_y, x + col_w - 2, top_y + header_h], outline=0, width=1)
            draw.text((x + col_w // 2, top_y + header_h // 2), day_label,
                      font=day_font, fill=0, anchor="mm")

        # Lunch cell
        cell_y = top_y + header_h + 1
        cell_x2 = x + col_w - 2
        cell_y2_lunch = cell_y + row_h - 1
        if is_today:
            draw.rectangle([x, cell_y, cell_x2, cell_y2_lunch], fill=0, outline=1, width=1)
        else:
            draw.rectangle([x, cell_y, cell_x2, cell_y2_lunch], outline=0, width=1)

        lunch_dishes = plan.get(date_str, {}).get('lunch', [])
        _draw_centered_dishes(draw, lunch_dishes, x, cell_y, col_w - 2, row_h - 1, dish_font_large, dish_font_small, is_today)

        # Dinner cell
        cell_y2 = cell_y + row_h
        cell_y2_dinner = cell_y2 + row_h - 1
        if is_today:
            draw.rectangle([x, cell_y2, cell_x2, cell_y2_dinner], fill=0, outline=1, width=1)
        else:
            draw.rectangle([x, cell_y2, cell_x2, cell_y2_dinner], outline=0, width=1)

        dinner_dishes = plan.get(date_str, {}).get('dinner', [])
        _draw_centered_dishes(draw, dinner_dishes, x, cell_y2, col_w - 2, row_h - 1, dish_font_large, dish_font_small, is_today)

    return img


def image_to_1bit_raw(img: Image.Image) -> bytes:
    """Convert PIL image to raw 1-bit bitmap for the e-ink display.

    Format: packed bits, MSB first, 1=white, 0=black.
    Each row is padded to full bytes: ceil(792/8) = 99 bytes per row.
    Total: 99 * 272 = 26,928 bytes.
    """
    if img.mode != '1':
        img = img.convert('1')

    if img.size != (DISPLAY_WIDTH, DISPLAY_HEIGHT):
        img = img.resize((DISPLAY_WIDTH, DISPLAY_HEIGHT), Image.LANCZOS)

    raw = bytearray()
    pixels = img.load()
    row_bytes = (DISPLAY_WIDTH + 7) // 8

    for y in range(DISPLAY_HEIGHT):
        for bx in range(row_bytes):
            byte = 0
            for bit in range(8):
                px = bx * 8 + bit
                if px < DISPLAY_WIDTH:
                    # PIL '1' mode: 0=black, 255=white
                    # Display convention: 0=white, 1=black (inverted)
                    if not pixels[px, y]:
                        byte |= (0x80 >> bit)  # Set bit = black
                # Bits beyond width stay 0 (black padding) - fine
            raw.append(byte)

    return bytes(raw)


def find_display_ip() -> Optional[str]:
    """Try to find the display on the local network."""
    hostname = socket.gethostname()
    local_ip = socket.gethostbyname(hostname)
    network_prefix = '.'.join(local_ip.split('.')[:3])

    print(f"Searching for display on {network_prefix}.x ...")

    for i in range(2, 255):
        ip = f"{network_prefix}.{i}"
        try:
            req = urllib.request.Request(f"http://{ip}/status", method='GET')
            req.add_header('User-Agent', 'MealSender/1.0')
            response = urllib.request.urlopen(req, timeout=0.5)
            data = json.loads(response.read().decode())
            if 'last_update' in data or 'image_size' in data:
                print(f"Found display at {ip}")
                return ip
        except Exception:
            pass

        if i % 50 == 0:
            print(f"  scanned {i} IPs...")

    return None


def send_to_display(ip: str, image_data: bytes) -> bool:
    """Send raw 1-bit image to the e-ink display via HTTP POST."""
    url = f"http://{ip}/upload"

    try:
        boundary = '----MealDisplayBoundary'

        body = bytearray()
        body.extend(f'--{boundary}\r\n'.encode())
        body.extend(b'Content-Disposition: form-data; name="image"; filename="mealplan.bin"\r\n')
        body.extend(b'Content-Type: application/octet-stream\r\n\r\n')
        body.extend(image_data)
        body.extend(f'\r\n--{boundary}--\r\n'.encode())

        req = urllib.request.Request(url, data=bytes(body), method='POST')
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

        response = urllib.request.urlopen(req, timeout=30)
        result = response.read().decode()
        print(f"Display response: {result}")
        return response.status == 200

    except urllib.error.URLError as e:
        print(f"Failed to send: {e}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description='Send meal plan to e-ink display')
    parser.add_argument('--ip', help='Display IP address (auto-detect if not specified)')
    parser.add_argument('--week', help='Week start date (YYYY-MM-DD), defaults to current week')
    parser.add_argument('--preview', action='store_true', help='Only generate image, don\'t send')
    parser.add_argument('--output', '-o', help='Save image to file')

    args = parser.parse_args()

    # Determine week
    if args.week:
        week_start = datetime.strptime(args.week, "%Y-%m-%d")
    else:
        week_start = get_week_start()

    print(f"Meal plan for week starting: {week_start.strftime('%Y-%m-%d')} ({DAYS_CN[week_start.weekday()]})")

    # Fetch meal plan
    try:
        plan = get_meal_plan(week_start)
        print(f"Found {len(plan)} days with planned meals")
    except FileNotFoundError as e:
        print(f"Error: {e}")
        print(f"\nMake sure meal-planner is running and database exists at:")
        print(f"  {MEAL_PLANNER_DB}")
        sys.exit(1)
    except Exception as e:
        print(f"Database error: {e}")
        sys.exit(1)

    # Generate image
    print(f"Generating {DISPLAY_WIDTH}x{DISPLAY_HEIGHT} B&W image...")
    img = create_meal_plan_image(week_start, plan)

    # Save if requested
    if args.output:
        img.save(args.output)
        print(f"Saved to: {args.output}")

    if args.preview:
        print("Preview mode - not sending to display")
        if not args.output:
            preview_path = "/tmp/meal_plan_preview.png"
            img.save(preview_path)
            print(f"Preview saved to: {preview_path}")
        return

    # Find display IP
    display_ip = args.ip
    if not display_ip:
        display_ip = find_display_ip()
        if not display_ip:
            print("Could not find display automatically.")
            print("Please specify IP with --ip or check that display is powered on and connected to WiFi")
            sys.exit(1)

    print(f"Sending to display at {display_ip}...")

    image_data = image_to_1bit_raw(img)
    expected = (DISPLAY_WIDTH + 7) // 8 * DISPLAY_HEIGHT
    print(f"Raw data: {len(image_data)} bytes (expected: {expected})")

    success = send_to_display(display_ip, image_data)

    if success:
        print("Done - meal plan sent successfully!")
    else:
        print("Failed to send meal plan")
        sys.exit(1)


if __name__ == '__main__':
    main()
