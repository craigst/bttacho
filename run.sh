#!/bin/bash
# Run the Tacho Downloader app

cd "$(dirname "$0")"

if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Try PyQt6 version first, fall back to Tkinter
python3 tacho_app.py 2>/dev/null || python3 tacho_app_tk.py
