# Perfboard Planner v38

v38 adds bulk wire routing tools on top of the Qt workflow. You can optimize the current side, the whole board, or allow via-based cross-side routing.

## Run

```bash
python -m pip install -r requirements.txt
python run.py
```

The older Tkinter fallback is still available:

```bash
python run_tk.py
```



## v38 changes

- Added **Suggest all** in Wire mode.
- Optimize existing wires on the current side while keeping endpoints.
- Optimize the whole board across front/back wires.
- Optional whole-board routing with via transitions to the opposite side.
- Router still treats component bodies and keepouts as hard no-go areas.

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
