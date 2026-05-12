# Perfboard Planner v36

v36 continues the Qt UI cleanup and focuses on canvas clarity, routing behavior, and view aids.

## Run

```bash
python -m pip install -r requirements.txt
python run.py
```

The older Tkinter fallback is still available:

```bash
python run_tk.py
```

## v36 changes

- Group outlines are only shown when a group is selected from the Groups tab, keeping the canvas uncluttered during normal editing.
- Selecting a group from the Groups tab selects the items in that group.
- Project settings only show in the inspector when Select mode is active and nothing is selected.
- View controls include toggles for current-side keepouts and opposite-side ghost keepouts.
- Row/column labels were added to the board.
- Label style can be numbers, letters, or both.
- Row and column labels can be toggled separately for front and back views.
- Added an optional Excel-like mouse crosshair that highlights the hovered row and column.
- Editing wire point text fields also highlights the currently edited row/column on the board.
- Removed the old floating wire-mode instruction box from the canvas.
- The bottom-bar Suggest route button is now the active route control.
- The Suggest route button uses the current new-wire color so it is easier to see.
- Suggested routing now treats component bodies as hard no-go areas.
- Suggested routing avoids existing wires when possible, but may cross wires when necessary.
- Suggested routes now fail cleanly instead of crossing through components when no safe route is available.
- Wire snapping now prefers the exact hole you clicked before using wider component-pin context snapping.

## Notes

The Qt frontend is the main UI. The core logic remains separated from the UI so the model, storage, checks, routing, and BOM systems can keep evolving safely.
