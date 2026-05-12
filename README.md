# Perfboard Planner v29

This version is an architecture pass. It keeps the current Tkinter frontend, but moves the reusable board logic into a package structure so the program is no longer just one growing script.

## Run

```bash
python run.py
```

or:

```bash
python -m perfboard_planner.app
```

## Project layout

```text
perfboard_planner_v29/
  run.py
  perfboard_planner/
    app.py
    core/
      models.py        # board data objects
      storage.py       # JSON loading/saving/migration
      geometry.py      # mirroring, rotation, grid helpers
      connectivity.py  # pure net graph helpers
      checks.py        # pure layout warning helpers
      routing.py       # route helper functions
      commands.py      # command stack foundation
    ui/
      tk_app.py        # current Tkinter frontend
```

## What changed internally

- The data model is now in `core/models.py`.
- JSON save/load is now in `core/storage.py`.
- Old `version` JSON files are still accepted.
- New saves use `schema_version` and `app_version`.
- Obsolete manual wire `layer` / `lane` data is loaded for compatibility but no longer written into new saved layout files.
- Connectivity helpers now follow the cleaner rule: only explicit wire points/bends are electrical connection points. A wire merely crossing a hole is not treated as connected.
- The current Tkinter UI is still available as `ui/tk_app.py` while future UI rewrites can reuse the core modules.

## Notes

This is not meant as a flashy feature version. It is a foundation version so future UI work can happen without fighting a single massive file.
