from __future__ import annotations


def main() -> None:
    """Start the modern Qt UI, falling back to the legacy Tkinter UI if PySide6 is unavailable."""
    print("Perfboard Planner: starting application.", flush=True)
    print("Perfboard Planner: loading the modern Qt frontend...", flush=True)
    try:
        from .ui.qt_app import run
    except ModuleNotFoundError as exc:
        if exc.name != "PySide6":
            raise
        print("Perfboard Planner: PySide6 is not installed; falling back to the legacy Tkinter frontend.", flush=True)
        print("Perfboard Planner: install the modern UI with: python -m pip install -r requirements.txt", flush=True)           
        from .ui.tk_app import PerfboardPlanner

        app = PerfboardPlanner()
        app.mainloop()
        return
    run()


if __name__ == "__main__":
    main()
