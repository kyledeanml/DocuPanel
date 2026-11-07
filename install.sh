#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
BIN_DIR="$HOME/.local/bin"
STYLES_DIR="$HOME/.local/share/gtksourceview-4/styles"

echo "=========================================================="
echo " Installing DocuPad for Linux Mint Cinnamon"
echo " (Document Viewer & Reader Companion for PythonPad)"
echo "=========================================================="

chmod +x "$DIR/run.sh" "$DIR/docupad.py"

mkdir -p "$APP_DIR"
mkdir -p "$ICONS_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$STYLES_DIR"

# Clean up any legacy docpad artifacts if present
rm -f "$APP_DIR/docpad.desktop" "$BIN_DIR/docpad" "$ICONS_DIR/docpad.svg" \
      "$HOME/.icons/docpad.svg" "$HOME/.local/share/pixmaps/docpad.svg" \
      "$STYLES_DIR/docpad-neon.xml" 2>/dev/null || true
rm -f "$HOME/.local/share/icons/hicolor/"*/apps/docpad.png 2>/dev/null || true

# 1. Install high-DPI vector icon & multi-size PNG icons for Cinnamon panel / menus
cp -f "$DIR/docupad.svg" "$ICONS_DIR/docupad.svg"
mkdir -p "$HOME/.icons" "$HOME/.local/share/pixmaps"
cp -f "$DIR/docupad.svg" "$HOME/.icons/docupad.svg"
cp -f "$DIR/docupad.svg" "$HOME/.local/share/pixmaps/docupad.svg"

python3 -B -c "
import os, gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf
src = '$DIR/docupad.svg'
base = os.path.expanduser('~/.local/share/icons/hicolor')
for sz in (16, 24, 32, 48, 64, 128, 256, 512):
    out_dir = os.path.join(base, f'{sz}x{sz}', 'apps')
    os.makedirs(out_dir, exist_ok=True)
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(src, sz, sz, True)
    pb.savev(os.path.join(out_dir, 'docupad.png'), 'png', [], [])
" 2>/dev/null || true

echo "[+] Installed DocuPad SVG & panel icon set: $ICONS_DIR/docupad.svg"

if [ -d "$DIR/styles" ]; then
    cp -r "$DIR/styles/"* "$STYLES_DIR/"
    echo "[+] Installed GtkSource style schemes: $STYLES_DIR"
fi

# 2. Install executable launcher in ~/.local/bin
ln -sf "$DIR/run.sh" "$BIN_DIR/docupad"
echo "[+] Created CLI executable link: $BIN_DIR/docupad"

# 3. Write desktop launcher for Cinnamon Menu and Panel
cat <<EOF > "$APP_DIR/docupad.desktop"
[Desktop Entry]
Name=DocuPad
Comment=Document viewer & reader companion with Retina Vision, Outline sidebar, and PDF/Markdown rendering
GenericName=Document Viewer & Reader
Exec=$DIR/run.sh %F
Icon=$ICONS_DIR/docupad.svg
Terminal=false
Type=Application
Categories=GTK;GNOME;Office;Viewer;Utility;TextEditor;
MimeType=text/markdown;text/x-markdown;application/pdf;text/html;text/x-rst;text/csv;application/json;application/vnd.oasis.opendocument.text;application/vnd.openxmlformats-officedocument.wordprocessingml.document;
StartupNotify=true
StartupWMClass=docupad
Keywords=document;viewer;reader;markdown;pdf;rst;html;docupad;outline;retina;
Actions=NewWindow;

[Desktop Action NewWindow]
Name=Open a New Window
Exec=$DIR/run.sh
EOF

chmod +x "$APP_DIR/docupad.desktop"
echo "[+] Installed desktop entry: $APP_DIR/docupad.desktop"

# 4. Refresh desktop database & icon caches
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

# 5. Register DocuPad as default handler for Markdown (.md) and RST (.rst) documents
DEFAULT_MIMES=(
    "text/markdown"
    "text/x-markdown"
    "text/x-rst"
)

if command -v xdg-mime >/dev/null 2>&1; then
    for mime in "${DEFAULT_MIMES[@]}"; do
        xdg-mime default docupad.desktop "$mime" 2>/dev/null || true
    done
    echo "[+] Registered DocuPad via xdg-mime as default handler for .md and .rst documents"
fi

if command -v gio >/dev/null 2>&1; then
    for mime in "${DEFAULT_MIMES[@]}"; do
        gio mime "$mime" docupad.desktop >/dev/null 2>&1 || true
    done
fi

# 6. Add DocuPad to Cinnamon Menu favorites
if command -v gsettings >/dev/null 2>&1; then
    python3 -B -c "
import gi
from gi.repository import Gio
try:
    settings = Gio.Settings.new('org.cinnamon')
    favs = [f for f in settings.get_strv('favorite-apps') if f != 'docpad.desktop']
    if 'docupad.desktop' not in favs:
        favs.append('docupad.desktop')
    settings.set_strv('favorite-apps', favs)
    print('[+] Added DocuPad to Cinnamon favorite apps')
except Exception:
    pass
" || true
fi

echo ""
echo "=========================================================="
echo " DocuPad Installation Complete!"
echo "=========================================================="
echo "You can launch DocuPad anytime from your Cinnamon Menu"
echo "(under Office / Favorites) or from terminal by typing:"
echo "  docupad"
echo "=========================================================="
