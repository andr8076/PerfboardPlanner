# Perfboard Planner v40

v40 is a QoL-focused release. It adds live placement previews, auto-numbering, keyboard nudging, quick canvas controls, recent parts, warning focus mode, safer delete, autosave/recovery, dirty-file title markers, and accept/reject review for bulk route suggestions.

## Run

```bash
python -m pip install -r requirements.txt
python run.py
```

The older Tkinter fallback is still available:

```bash
python run_tk.py
```

## v40 changes

- Component/via/annotation/keepout tools now show a live preview before placement.
- New components are auto-numbered from their template name, for example `R1`, `R2`, `U1`.
- Arrow keys nudge selected items one hole; `Shift + Arrow` nudges five holes.
- Selected components show small on-canvas quick actions for rotate, side swap, lock/unlock, and pin editing.
- Recently used component templates appear in the top bar for quick reuse.
- Warning focus mode dims the layout and emphasizes warning points.
- Deleting multiple items asks for confirmation; deleting one item remains immediate.
- Unsaved changes show `*` in the window title.
- Autosave writes a recovery file and offers restore on next launch after an unsaved session.
- Suggest all now shows the proposed route changes and asks whether to keep or reject them.

## Recent previous changes

- Routing avoids other component pins, component bodies, and keepout zones.
- Middle mouse panning works in every mode.
- Row/column labels are cleaner and configurable per side.
- Crosshair is on by default.
- Keepout zones can be dragged in Select mode.

## Notes

The Qt frontend is the main UI. The core logic remains separated from the UI so the model, storage, checks, routing, and BOM systems can keep evolving safely.
