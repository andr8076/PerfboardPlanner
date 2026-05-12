# Perfboard Planner v39

v39 polishes the board interaction: routing avoids other component pins, middle-mouse panning works in every mode, row/column labels are cleaner and configurable per side, crosshair starts on, and keepout zones can be dragged.

## Run

```bash
python -m pip install -r requirements.txt
python run.py
```

The older Tkinter fallback is still available:

```bash
python run_tk.py
```



## v39 changes

- Suggested routing now treats other component pins as no-go cells, while still allowing the two selected endpoints.
- Middle mouse panning works in every mode, not only Select mode.
- Row/column labels use cleaner slim rails instead of crowded per-hole badges.
- Front and back label styles are configured independently: numbers, letters, or both.
- Mouse row/column crosshair is on by default.
- Keepout zones can be dragged in Select mode.
- Dragging selected items no longer requires the initial click to land exactly on a board hole.

## v37 hotfix

- Fixed a route-suggestion bug that could freeze the app and grow RAM usage.
- The route finder now stores direction-aware parent states and has a hard search cap.

## v37 changes

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
