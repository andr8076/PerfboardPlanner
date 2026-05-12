# Perfboard Planner v34

v34 refines the Qt workflow based on real use: route suggestion snaps to visible component pins, the Suggest Route action lives in the bottom board bar, Part/Wire settings only appear when those modes are active, groups now work as useful modules, and warnings can be muted when you intentionally accept them. The older Tkinter UI is still included as a fallback.

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

The app uses a modern editor layout:

- top toolbar for file actions, undo/redo, modes, warning count/status, and BOM
- central zoomable board canvas with a wire-color swatch rail above it
- bottom footer with Front/Back controls on the left, contextual wire routing in the middle, and zoom controls on the right
- left dock for objects, useful groups/modules, warnings with mute controls, and footprint library
- right inspector for selected-object editing, project settings, and contextual Part/Wire tools
- visual pin-layout editor for component pins and internal jumpers
- status bar with physical front/back hole mapping

The back side is shown as a physical mirrored view by default.

## Main interactions

- `Ctrl + mouse wheel` zooms around the pointer
- mouse wheel pans vertically
- `Shift + mouse wheel` pans horizontally
- middle/right drag pans the board in Select mode
- Select mode: click objects, Shift/Ctrl-click for multi-select, drag to move
- Part mode: click a hole to place the current component template; Part settings appear in the inspector only while Part mode is active
- Wire mode: click start, Shift-click bends, normal click finishes; the bottom-bar route helper lets you click two board holes/pins to create a context-aware suggested route
- Via mode: click a hole to toggle a front/back via
- Note mode: click a hole to add an annotation
- Keepout mode: click two corners to create a keepout zone
- Groups tab: select, hide/show, lock/unlock, rename, or assign items to modules
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

## Warning muting

The Warnings button now changes state:

- green/no count: no active warnings
- orange with a number: active warnings exist
- grey muted icon: only muted warnings remain

In the Warnings tab, use **Mute selected**, **Mute current**, or **Clear muted**. Muted warnings are not counted on the toolbar and are not highlighted on the board until unmuted.

## Groups / modules

Groups are now useful modules instead of just a text field. Use the Groups tab to select, hide/show, rename, lock/unlock, or assign selected items to a group. Group outlines are shown on the board so circuit sections are easier to move or review together.
