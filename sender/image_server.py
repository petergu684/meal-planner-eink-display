#!/usr/bin/env python3
"""
Meal Plan Image Server

Serves raw 1-bit meal plan images on demand.
The ESP32 display fetches images from this server when buttons are pressed.

  GET /meal_image?offset=0   -> current week
  GET /meal_image?offset=-1  -> last week
  GET /meal_image?offset=1   -> next week

Run:
    python3 image_server.py              # default port 5000
    python3 image_server.py --port 5000  # explicit port
"""

import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timedelta
import sys
import os

# Import the sender's image generation functions
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from send_meal_plan import (
    get_week_start, get_meal_plan, create_meal_plan_image, image_to_1bit_raw,
    DISPLAY_WIDTH, DISPLAY_HEIGHT
)


class MealImageHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == '/meal_image':
            params = parse_qs(parsed.query)
            offset = int(params.get('offset', ['0'])[0])

            today = datetime.now()
            week_start = get_week_start(today + timedelta(weeks=offset))

            self.log_message("Generating image for week %s (offset=%d)",
                             week_start.strftime('%Y-%m-%d'), offset)

            try:
                plan = get_meal_plan(week_start)
            except FileNotFoundError:
                plan = {}
            except Exception as e:
                self.log_message("DB error: %s", str(e))
                plan = {}

            img = create_meal_plan_image(week_start, plan)
            raw = image_to_1bit_raw(img)

            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        elif parsed.path == '/health':
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')

        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {format % args}")


def main():
    parser = argparse.ArgumentParser(description='Meal plan image server')
    parser.add_argument('--port', type=int, default=5000, help='Port (default 5000)')
    parser.add_argument('--bind', default='0.0.0.0', help='Bind address')
    args = parser.parse_args()

    httpd = HTTPServer((args.bind, args.port), MealImageHandler)
    print(f"Meal image server running on {args.bind}:{args.port}")
    print(f"  GET /meal_image?offset=0  -> current week")
    print(f"  GET /meal_image?offset=-1 -> previous week")
    print(f"  GET /health               -> health check")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down")
        httpd.server_close()


if __name__ == '__main__':
    main()
