from perfboard_planner.ui.tk_app import PerfboardPlanner

if __name__ == "__main__":
    print("Perfboard Planner: starting legacy Tkinter frontend.", flush=True)
    app = PerfboardPlanner()
    print("Perfboard Planner: Tkinter window is open.", flush=True)
    app.mainloop()
