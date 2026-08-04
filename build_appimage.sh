#!/bin/bash
# Build script for Tacho Downloader AppImage

set -e

APP_NAME="TachoDownloader"
APP_VERSION="1.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="$SCRIPT_DIR/build"
DIST_DIR="$SCRIPT_DIR/dist"
APPDIR="$BUILD_DIR/$APP_NAME.AppDir"

echo "=========================================="
echo "  Building $APP_NAME v$APP_VERSION"
echo "=========================================="

# Clean previous builds
rm -rf "$BUILD_DIR" "$DIST_DIR"
mkdir -p "$BUILD_DIR" "$DIST_DIR"

# Create virtual environment for build
echo ""
echo "[1/6] Setting up build environment..."
python3 -m venv "$BUILD_DIR/venv"
source "$BUILD_DIR/venv/bin/activate"

# Install dependencies
echo ""
echo "[2/6] Installing dependencies..."
pip install --upgrade pip wheel
pip install pyscard PyQt6 pyinstaller

# Build with PyInstaller
echo ""
echo "[3/6] Building executable with PyInstaller..."
pyinstaller \
    --name="$APP_NAME" \
    --onedir \
    --windowed \
    --noconfirm \
    --clean \
    --distpath="$DIST_DIR" \
    --workpath="$BUILD_DIR/pyinstaller" \
    --specpath="$BUILD_DIR" \
    --add-data="downloads:downloads" \
    "$SCRIPT_DIR/tacho_app.py" 2>/dev/null || \
pyinstaller \
    --name="$APP_NAME" \
    --onedir \
    --windowed \
    --noconfirm \
    --clean \
    --distpath="$DIST_DIR" \
    --workpath="$BUILD_DIR/pyinstaller" \
    --specpath="$BUILD_DIR" \
    "$SCRIPT_DIR/tacho_app.py"

# Create AppDir structure
echo ""
echo "[4/6] Creating AppDir structure..."
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/lib"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

# Copy the built application
cp -r "$DIST_DIR/$APP_NAME"/* "$APPDIR/usr/bin/"

# Create desktop file
cat > "$APPDIR/$APP_NAME.desktop" << EOF
[Desktop Entry]
Name=Tacho Downloader
Comment=Tachograph Card Downloader
Exec=TachoDownloader
Icon=tacho-downloader
Type=Application
Categories=Utility;
Terminal=false
EOF

cp "$APPDIR/$APP_NAME.desktop" "$APPDIR/usr/share/applications/"

# Create icon (simple SVG)
cat > "$APPDIR/tacho-downloader.svg" << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="256" height="256" viewBox="0 0 256 256" xmlns="http://www.w3.org/2000/svg">
  <rect width="256" height="256" rx="40" fill="#1e1e1e"/>
  <rect x="48" y="64" width="160" height="100" rx="8" fill="#2196F3"/>
  <rect x="64" y="80" width="48" height="32" rx="4" fill="#ffffff"/>
  <circle cx="188" cy="96" r="12" fill="#4CAF50"/>
  <rect x="64" y="124" width="128" height="8" rx="4" fill="#ffffff" opacity="0.7"/>
  <rect x="64" y="140" width="96" height="8" rx="4" fill="#ffffff" opacity="0.5"/>
  <path d="M128 180 L148 200 L108 200 Z" fill="#4CAF50"/>
  <rect x="124" y="200" width="8" height="24" fill="#4CAF50"/>
</svg>
EOF

# Convert SVG to PNG if possible, or use a simple fallback
if command -v convert &> /dev/null; then
    convert -background none "$APPDIR/tacho-downloader.svg" -resize 256x256 "$APPDIR/tacho-downloader.png"
else
    # Create a simple 256x256 PNG placeholder
    echo "Note: ImageMagick not found, using SVG icon"
fi

cp "$APPDIR/tacho-downloader.svg" "$APPDIR/usr/share/icons/hicolor/256x256/apps/"

# Create AppRun script
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
SELF=$(readlink -f "$0")
HERE=${SELF%/*}
export PATH="${HERE}/usr/bin/:${PATH}"
export LD_LIBRARY_PATH="${HERE}/usr/lib/:${LD_LIBRARY_PATH}"
exec "${HERE}/usr/bin/TachoDownloader" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# Download appimagetool if not present
APPIMAGETOOL="$BUILD_DIR/appimagetool-x86_64.AppImage"
if [ ! -f "$APPIMAGETOOL" ]; then
    echo ""
    echo "[5/6] Downloading appimagetool..."
    wget -q "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage" -O "$APPIMAGETOOL"
    chmod +x "$APPIMAGETOOL"
fi

# Build AppImage
echo ""
echo "[6/6] Building AppImage..."
ARCH=x86_64 "$APPIMAGETOOL" "$APPDIR" "$DIST_DIR/${APP_NAME}-${APP_VERSION}-x86_64.AppImage" 2>/dev/null || \
ARCH=x86_64 "$APPIMAGETOOL" --no-appstream "$APPDIR" "$DIST_DIR/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"

# Cleanup
deactivate
rm -rf "$BUILD_DIR/venv"

echo ""
echo "=========================================="
echo "  Build Complete!"
echo "=========================================="
echo ""
echo "  AppImage: $DIST_DIR/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
echo ""
echo "  To run:"
echo "    chmod +x $DIST_DIR/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
echo "    ./$DIST_DIR/${APP_NAME}-${APP_VERSION}-x86_64.AppImage"
echo ""
