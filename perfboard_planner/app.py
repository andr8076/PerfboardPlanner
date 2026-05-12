from __future__ import annotations

from .ui.tk_app import PerfboardPlanner


def main() -> None:
    app = PerfboardPlanner()
    app.mainloop()


if __name__ == "__main__":
    main()
