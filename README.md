# DocuPad

<p align="center">
  <img src="docupad.svg" alt="DocuPad Logo" width="160" height="160">
</p>

A fast, modern **Document Viewer, Reader, and Markdown/PDF Companion Application** with **Retina Vision** (HiDPI dynamic font scaling and subpixel anti-aliasing), **Document Outline Sidebar**, **Rendered Reader View & Source View**, **Multi-Tab Document Browsing**, and a translucent darker-blue open book and document watermark resting on an unassuming yoga pad. Built natively for **Linux Mint Cinnamon** using Python 3, GTK 3, GtkSourceView 4, and WebKit2.

---

## Key Features

### Multi-Format Document Viewing & Rendering
- **Rendered Reader View (`F6` / `Ctrl + P`)**: View Markdown (`.md`), reStructuredText (`.rst`), HTML (`.html`), CSV/TSV tables (`.csv`, `.tsv`), ODT (`.odt`), and DOCX (`.docx`) rendered with clean dark-neon typography, formatted tables, blockquotes, and code blocks.
- **High-DPI PDF Page Viewer (`.pdf`)**: Inspect PDF documents with crisp page-by-page rasterization (`pdftoppm`), zoom scaling, and instant text extraction (`pdftotext`) in Source View.
- **Source & Text View**: Inspect or edit the underlying document source with syntax highlighting, line numbers, and the translucent DocuPad yoga-pad watermark.
- **Read-Only Lock (`Ctrl + R`)**: Toggle read-only mode anytime to prevent accidental edits while reading documentation.

### Document Outline & Table of Contents Sidebar
- **Automatic Heading Extraction (`F9`)**: Automatically builds a hierarchical Table of Contents from Markdown headings (`#` through `######`), reStructuredText underlined sections, HTML `<h1>`–`<h6>` tags, JSON top-level keys, or PDF pages.
- **One-Click Section Jump**: Click any heading or page entry in the Outline sidebar to jump directly to that section or PDF page.

### Companion Integration with PythonPad
- **Open in PythonPad**: Launch the current file directly in PythonPad from *Menu -> Open in PythonPad...*
- **Unified Dark Mode & Neon Blue Aesthetic**: Matches PythonPad's dark slate UI (`#0b0f19`), crisp white text, neon cyan/blue accents (`docupad-neon`), and translucent yoga-pad emblem (`docupad.svg`).

### Retina Vision & Navigation
- **Dynamic Retina Zooming**: Scale both Rendered Reader View, PDF pages, and Source View fluidly from **50% up to 300%** using `Ctrl + +`, `Ctrl + -`, `Ctrl + 0`, or **`Ctrl + Mouse Wheel`**.
- **Integrated Search & Replace (`Ctrl + F` / `Ctrl + H`)**: Live match counting, case sensitivity, whole-word matching, and regular expressions.
- **HTML Export**: Export any Markdown, RST, CSV, or text document to a standalone styled dark-mode HTML file (*Menu -> Export Document to HTML...*).

---

## Installation

```bash
./install.sh
```

The installer automatically:
- Installs the vector icon (`docupad.svg`) and multi-size panel icon set (`16x16` through `512x512`) into `~/.local/share/icons/hicolor/`.
- Installs the desktop launcher (`~/.local/share/applications/docupad.desktop`) with `StartupWMClass=docupad`.
- Creates the CLI command symlink at `~/.local/bin/docupad`.
- Registers DocuPad as the default viewer for `.md` and `.rst` files and pins DocuPad to your Cinnamon Menu favorites.

---

## Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `F6` / `Ctrl + P` | **Toggle Rendered Reader View / Source View** |
| `F9` | **Toggle Document Outline Sidebar** |
| `Ctrl + R` | **Toggle Read-Only Lock** |
| `Ctrl + T` / `Ctrl + N` | **New Document Tab** |
| `Ctrl + O` | **Open Document** |
| `Ctrl + S` / `Ctrl + Shift + S` | **Save / Save As** |
| `Ctrl + W` | **Close Current Tab** |
| `Ctrl + F` / `Ctrl + H` | **Find / Replace Bar** |
| `Ctrl + G` | **Go to Line** |
| `Ctrl + +` / `Ctrl + -` | **Retina Zoom In / Out** |
| `Ctrl + 0` | **Reset Retina Zoom (100%)** |
| `Ctrl + Scroll Wheel` | **Smooth Real-Time Zooming** |
| `Ctrl + Q` | **Quit Application** |
