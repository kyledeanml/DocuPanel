#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
BIN_DIR="$HOME/.local/bin"
STYLES_DIR="$HOME/.local/share/gtksourceview-4/styles"

echo "=========================================================="
echo " Installing DocPad for Linux Mint Cinnamon"
echo " (Document Viewer & Reader Companion for PythonPad)"
echo "=========================================================="

chmod +x "$DIR/run.sh" "$DIR/docpad.py"

mkdir -p "$APP_DIR"
mkdir -p "$ICONS_DIR"
mkdir -p "$BIN_DIR"
mkdir -p "$STYLES_DIR"

# 1. Install high-DPI vector icon & multi-size PNG icons for Cinnamon panel / menus
cp -f "$DIR/docpad.svg" "$ICONS_DIR/docpad.svg"
mkdir -p "$HOME/.icons" "$HOME/.local/share/pixmaps"
cp -f "$DIR/docpad.svg" "$HOME/.icons/docpad.svg"
cp -f "$DIR/docpad.svg" "$HOME/.local/share/pixmaps/docpad.svg"

python3 -B -c "
import os, gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf
src = '$DIR/docpad.svg'
base = os.path.expanduser('~/.local/share/icons/hicolor')
for sz in (16, 24, 32, 48, 64, 128, 256, 512):
    out_dir = os.path.join(base, f'{sz}x{sz}', 'apps')
    os.makedirs(out_dir, exist_ok=True)
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(src, sz, sz, True)
    pb.savev(os.path.join(out_dir, 'docpad.png'), 'png', [], [])
" 2>/dev/null || true

echo "[+] Installed DocPad SVG & panel icon set: $ICONS_DIR/docpad.svg"

if [ -d "$DIR/styles" ]; then
    cp -r "$DIR/styles/"* "$STYLES_DIR/"
    echo "[+] Installed GtkSource style schemes: $STYLES_DIR"
fi

# 2. Install executable launcher in ~/.local/bin
ln -sf "$DIR/run.sh" "$BIN_DIR/docpad"
echo "[+] Created CLI executable link: $BIN_DIR/docpad"

# 3. Write desktop launcher for Cinnamon Menu and Panel
cat <<EOF > "$APP_DIR/docpad.desktop"
[Desktop Entry]
Name=DocPad
Comment=Document viewer & reader companion with Retina Vision, Outline sidebar, and PDF/Markdown rendering
GenericName=Document Viewer & Reader
Exec=$DIR/run.sh %F
Icon=$ICONS_DIR/docpad.svg
Terminal=false
Type=Application
Categories=GTK;GNOME;Office;Viewer;Utility;TextEditor;
MimeType=text/markdown;text/x-markdown;application/pdf;text/html;text/x-rst;text/csv;application/json;application/vnd.oasis.opendocument.text;application/vnd.openxmlformats-officedocument.wordprocessingml.document;
StartupNotify=true
StartupWMClass=docpad
Keywords=document;viewer;reader;markdown;pdf;rst;html;docpad;outline;retina;
Actions=NewWindow;

[Desktop Action NewWindow]
Name=Open a New Window
Exec=$DIR/run.sh
EOF

chmod +x "$APP_DIR/docpad.desktop"
echo "[+] Installed desktop entry: $APP_DIR/docpad.desktop"

# 4. Refresh desktop database & icon caches
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APP_DIR" || true
fi

if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi

# 5. Register DocPad as default handler for Markdown (.md) and RST (.rst) documents
DEFAULT_MIMES=(
    "text/markdown"
    "text/x-markdown"
    "text/x-rst"
)

if command -v xdg-mime >/dev/null 2>&1; then
    for mime in "${DEFAULT_MIMES[@]}"; do
        xdg-mime default docpad.desktop "$mime" 2>/dev/null || true
    done
    echo "[+] Registered DocPad via xdg-mime as default handler for .md and .rst documents"
fi

if command -v gio >/dev/null 2>&1; then
    for mime in "${DEFAULT_MIMES[@]}"; do
        gio mime "$mime" docpad.desktop >/dev/null 2>&1 || true
    done
fi

# 6. Add DocPad to Cinnamon Menu favorites
if command -v gsettings >/dev/null 2>&1; then
    python3 -B -c "
import gi
from gi.repository import Gio
try:
    settings = Gio.Settings.new('org.cinnamon')
    favs = list(settings.get_strv('favorite-apps'))
    if 'docpad.desktop' not in favs:
        favs.append('docpad.desktop')
    settings.set_strv('favorite-apps', favs)
    print('[+] Added DocPad to Cinnamon favorite apps')
except Exception:
    pass
" || true
fi

echo ""
echo "=========================================================="
echo " DocPad Installation Complete!"
echo "=========================================================="
echo "You can launch DocPad anytime from your Cinnamon Menu"
echo "(under Office / Favorites) or from terminal by typing:"
echo "  docpad"
echo "=========================================================="
