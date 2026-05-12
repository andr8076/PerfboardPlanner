# Perfboard Planner v32

v32 refines the Qt workflow: wire color editing now offers existing used colors, Escape returns to Select mode, Front/Back controls live in the canvas footer opposite the zoom controls with a small flip cue, keepouts no longer move in Select mode, and route suggestion now works by clicking two board holes. The older Tkinter UI is still included as a fallback.

## Install

```bash
python -m pip install -r requirements.txt
```

## Run

```bash
python run.py
```

This starts the Qt UI when PySide6 is installed. If PySide6 is missing, it falls back to the legacy Tkinter frontend and prints the install command.

You can also run a specific frontend:

```bash
python run_qt.py
python run_tk.py
```

## Current Qt UI direction

The app now uses a modern editor layout:

- top toolbar for file actions, undo/redo, modes, warnings, and BOM
- central zoomable board canvas with a wire-color swatch rail above it
- bottom footer with Front/Back controls on the left and zoom controls on the right
- left dock for objects, warnings, and footprint library
- right inspector for editing selected objects and project settings
- visual pin-layout editor for component pins and internal jumpers
- status bar with physical front/back hole mapping

The back side is still shown as a physical mirrored view by default.

## Main interactions

- `Ctrl + mouse wheel` zooms around the pointer
- mouse wheel pans vertically
- `Shift + mouse wheel` pans horizontally
- middle/right drag pans the board in Select mode
- Select mode: click objects, Shift/Ctrl-click for multi-select, drag to move
- Part mode: click a hole to place the current component template
- Wire mode: click start, Shift-click bends, normal click finishes; the route helper lets you click two board holes to create a suggested dogleg route
- Via mode: click a hole to toggle a front/back via
- Note mode: click a hole to add an annotation
- Keepout mode: click two corners to create a keepout zone
- `Delete`/`Backspace` deletes selected unlocked items
- `Esc` exits back to Select mode
- `R` rotates selected components visually

## Architecture

The core model is separated from the UI:

- `perfboard_planner/core/models.py`
- `perfboard_planner/core/storage.py`
- `perfboard_planner/core/geometry.py`
- `perfboard_planner/core/connectivity.py`
- `perfboard_planner/core/checks.py`
- `perfboard_planner/core/routing.py`
- `perfboard_planner/core/bom.py`
- `perfboard_planner/ui/qt/` for the new UI
- `perfboard_planner/ui/tk_app.py` for the legacy UI

Older JSON layouts should still load. New saves use `schema_version` and `app_version` metadata.
