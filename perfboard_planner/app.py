from __future__ import annotations


def main() -> None:
    """Start the modern Qt UI, falling back to the legacy Tkinter UI if PySide6 is unavailable."""
    try:
        from .ui.qt_app import run
    except ModuleNotFoundError as exc:
        if exc.name != "PySide6":
            raise
        print("PySide6 is not installed. Falling back to the legacy Tkinter frontend.")
        print("Install the modern UI with: python -m pip install -r requirements.txt")
        from .ui.tk_app import PerfboardPlanner

        app = PerfboardPlanner()
        app.mainloop()
        return
    run()


if __name__ == "__main__":
    main()
