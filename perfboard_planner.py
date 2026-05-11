import json
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from dataclasses import dataclass, asdict
from typing import List, Optional, Tuple, Dict, Any


@dataclass
class Component:
    name: str
    row: int
    col: int
    width: int
    height: int
    color: str


@dataclass
class Wire:
    name: str
    points: List[Tuple[int, int]]  # [(row, col), ...]
    color: str


class PerfboardPlanner(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Perfboard Planner")
        self.geometry("1200x800")

        self.rows = 30
        self.cols = 45
        # Base board geometry. Zoom only changes how this is drawn on screen;
        # it does not change the saved board layout.
        self.spacing = 22
        self.margin = 35
        self.hole_radius = 4
        self.zoom = 1.0
        self.min_zoom = 0.45
        self.max_zoom = 2.75

        self.components: List[Component] = []
        self.wires: List[Wire] = []

        self.mode = tk.StringVar(value="select")
        self.current_color = tk.StringVar(value="#ffcc66")
        self.current_wire_color = tk.StringVar(value="#d00000")
        self.current_name = tk.StringVar(value="Part")
        self.component_w = tk.IntVar(value=4)
        self.component_h = tk.IntVar(value=2)

        self.selected_kind: Optional[str] = None
        self.selected_index: Optional[int] = None
        self.temp_wire_points: List[Tuple[int, int]] = []
        self.preview_line_id: Optional[int] = None

        self.mode_styles = {
            "select": {
                "label": "SELECT / MOVE",
                "color": "#20639b",
                "hint": "Click an item to select it. Drag components to move them. Delete/Backspace removes the selected item.",
            },
            "component": {
                "label": "ADD COMPONENT",
                "color": "#c47f00",
                "hint": "Click a hole to place the current component size and color.",
            },
            "wire": {
                "label": "DRAW WIRE",
                "color": "#b00020",
                "hint": "Click a start hole, then click an end hole. Shift+click adds bend points. Right-click or Enter finishes the current wire.",
            },
            "label": {
                "label": "TEXT LABEL",
                "color": "#6a1b9a",
                "hint": "Click a hole to place a text label.",
            },
        }

        self._build_ui()
        self.redraw()

    def _build_ui(self):
        root = ttk.Frame(self)
        root.pack(fill=tk.BOTH, expand=True)

        side = ttk.Frame(root, padding=10)
        side.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(side, text="Tool", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        for text, value in [("Select / move", "select"), ("Add component", "component"), ("Draw wire", "wire"), ("Text label", "label")]:
            ttk.Radiobutton(side, text=text, variable=self.mode, value=value, command=self._mode_changed).pack(anchor="w", pady=2)

        ttk.Separator(side).pack(fill=tk.X, pady=10)

        ttk.Label(side, text="Component", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(side, text="Name").pack(anchor="w")
        ttk.Entry(side, textvariable=self.current_name, width=18).pack(anchor="w", fill=tk.X)

        size_row = ttk.Frame(side)
        size_row.pack(anchor="w", pady=4)
        ttk.Label(size_row, text="W").pack(side=tk.LEFT)
        ttk.Spinbox(size_row, from_=1, to=30, textvariable=self.component_w, width=4).pack(side=tk.LEFT, padx=(3, 8))
        ttk.Label(size_row, text="H").pack(side=tk.LEFT)
        ttk.Spinbox(size_row, from_=1, to=30, textvariable=self.component_h, width=4).pack(side=tk.LEFT, padx=3)

        ttk.Button(side, text="Component color", command=self.choose_component_color).pack(fill=tk.X, pady=3)
        ttk.Button(side, text="Wire color", command=self.choose_wire_color).pack(fill=tk.X, pady=3)

        ttk.Separator(side).pack(fill=tk.X, pady=10)

        ttk.Label(side, text="Board", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        board_row = ttk.Frame(side)
        board_row.pack(anchor="w", pady=4)
        ttk.Button(board_row, text="Resize", command=self.resize_board).pack(side=tk.LEFT)
        ttk.Button(board_row, text="Clear", command=self.clear_board).pack(side=tk.LEFT, padx=5)

        zoom_row = ttk.Frame(side)
        zoom_row.pack(anchor="w", pady=(2, 4), fill=tk.X)
        ttk.Button(zoom_row, text="−", width=3, command=lambda: self.zoom_step(1 / 1.15)).pack(side=tk.LEFT)
        self.zoom_label = tk.StringVar(value="100%")
        ttk.Label(zoom_row, textvariable=self.zoom_label, width=7, anchor="center").pack(side=tk.LEFT, padx=4)
        ttk.Button(zoom_row, text="+", width=3, command=lambda: self.zoom_step(1.15)).pack(side=tk.LEFT)
        ttk.Button(zoom_row, text="Reset", command=self.reset_zoom).pack(side=tk.LEFT, padx=(5, 0))

        ttk.Separator(side).pack(fill=tk.X, pady=10)

        ttk.Label(side, text="File", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Button(side, text="New", command=self.new_file).pack(fill=tk.X, pady=2)
        ttk.Button(side, text="Open JSON", command=self.open_file).pack(fill=tk.X, pady=2)
        ttk.Button(side, text="Save JSON", command=self.save_file).pack(fill=tk.X, pady=2)
        ttk.Button(side, text="Export PNG", command=self.export_png).pack(fill=tk.X, pady=2)

        ttk.Separator(side).pack(fill=tk.X, pady=10)

        help_text = (
            "Use the holes as snap points.\n\n"
            "View:\n"
            "  Ctrl + wheel = zoom\n"
            "  + / - = zoom in/out\n"
            "  0 = reset zoom\n"
            "  middle-drag = pan\n"
            "  right-drag = pan, except while finishing a wire\n"
            "  mouse wheel = vertical scroll\n"
            "  Shift + wheel = horizontal scroll\n\n"
            "Wire mode:\n"
            "  click start, click end = add wire\n"
            "  Shift+click = add bend point\n"
            "  right-click / Enter = finish wire\n"
            "  Esc = cancel wire\n\n"
            "Select mode:\n"
            "  click item to select\n"
            "  drag component to move\n"
            "  Delete / Backspace = remove selected"
        )
        ttk.Label(side, text=help_text, justify=tk.LEFT).pack(anchor="w", pady=5)

        self.status = tk.StringVar(value="Ready")
        ttk.Label(side, textvariable=self.status, wraplength=190).pack(anchor="w", side=tk.BOTTOM)

        board_area = ttk.Frame(root)
        board_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.mode_banner = tk.Label(
            board_area,
            text="",
            anchor="w",
            padx=12,
            pady=7,
            fg="#ffffff",
            font=("TkDefaultFont", 12, "bold"),
        )
        self.mode_banner.pack(side=tk.TOP, fill=tk.X)

        self.canvas_border = tk.Frame(board_area, bg="#20639b", padx=4, pady=4)
        self.canvas_border.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.canvas_border.rowconfigure(0, weight=1)
        self.canvas_border.columnconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self.canvas_border,
            bg="#f2f2f2",
            highlightthickness=0,
            takefocus=True,
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        v_scroll = ttk.Scrollbar(self.canvas_border, orient=tk.VERTICAL, command=self.canvas.yview)
        h_scroll = ttk.Scrollbar(self.canvas_border, orient=tk.HORIZONTAL, command=self.canvas.xview)
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        self.canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

        self.canvas.bind("<Button-1>", self.on_click)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_motion)

        # Camera / viewport controls.
        self.canvas.bind("<ButtonPress-2>", self.start_pan)
        self.canvas.bind("<B2-Motion>", self.do_pan)
        self.canvas.bind("<ButtonPress-3>", self.on_right_click)
        self.canvas.bind("<B3-Motion>", self.on_right_drag)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", self.on_linux_wheel_up)
        self.canvas.bind("<Button-5>", self.on_linux_wheel_down)
        self.canvas.bind("<Key-plus>", self.zoom_in_key)
        self.canvas.bind("<Key-equal>", self.zoom_in_key)
        self.canvas.bind("<Key-minus>", self.zoom_out_key)
        self.canvas.bind("<Key-0>", self.reset_zoom_key)
        # Keyboard actions work after the canvas has focus. The canvas takes focus
        # whenever the user clicks the board, so these do not hijack text editing in
        # the sidebar fields.
        self.canvas.bind("<Delete>", self.delete_selected)
        self.canvas.bind("<BackSpace>", self.delete_selected)
        self.canvas.bind("<Escape>", self.cancel_temp_wire)
        self.canvas.bind("<Return>", lambda event: self.finish_temp_wire(event, ask_name=True))
        self.bind("<Delete>", self.delete_selected)
        self.bind("<BackSpace>", self.delete_selected)
        self.bind("<Escape>", self.cancel_temp_wire)
        self.bind("<Return>", lambda event: self.finish_temp_wire(event, ask_name=True))
        self.bind("<Key-plus>", self.zoom_in_key)
        self.bind("<Key-equal>", self.zoom_in_key)
        self.bind("<Key-minus>", self.zoom_out_key)
        self.bind("<Key-0>", self.reset_zoom_key)

        self.drag_start_grid: Optional[Tuple[int, int]] = None
        self.drag_component_original: Optional[Tuple[int, int]] = None
        self.update_zoom_label()
        self._update_mode_ui()

    def _mode_changed(self):
        self.cancel_temp_wire()
        self.selected_kind = None
        self.selected_index = None
        self._update_mode_ui()
        self.status.set(self._mode_style()["hint"])
        self.redraw()

    def _mode_style(self) -> Dict[str, str]:
        return self.mode_styles.get(self.mode.get(), self.mode_styles["select"])

    def _update_mode_ui(self):
        if not hasattr(self, "mode_banner"):
            return
        style = self._mode_style()
        self.mode_banner.configure(text=f"{style['label']}  —  {style['hint']}", bg=style["color"])
        self.canvas_border.configure(bg=style["color"])

    def canvas_event_xy(self, event) -> Tuple[float, float]:
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    @staticmethod
    def shift_is_down(event) -> bool:
        # Tk uses bit 0x0001 for Shift on the main desktop platforms.
        return bool(getattr(event, "state", 0) & 0x0001)

    @staticmethod
    def control_is_down(event) -> bool:
        # Tk uses bit 0x0004 for Control on the main desktop platforms.
        return bool(getattr(event, "state", 0) & 0x0004)

    def event_from_text_input(self, event) -> bool:
        focus = self.focus_get()
        if focus is None or focus == self.canvas:
            return False
        return focus.winfo_class() in {"Entry", "TEntry", "Spinbox", "TSpinbox", "Text"}

    def start_pan(self, event):
        self.canvas.focus_set()
        self.canvas.scan_mark(event.x, event.y)
        return "break"

    def do_pan(self, event):
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        return "break"

    def on_right_click(self, event):
        self.canvas.focus_set()
        if self.mode.get() == "wire" and self.temp_wire_points:
            self.finish_temp_wire(event, ask_name=False)
            return "break"
        return self.start_pan(event)

    def on_right_drag(self, event):
        if self.mode.get() == "wire" and self.temp_wire_points:
            return "break"
        return self.do_pan(event)

    def on_mouse_wheel(self, event):
        if self.control_is_down(event):
            self.zoom_at_event(event, 1.12 if event.delta > 0 else 1 / 1.12)
            return "break"

        direction = -1 if event.delta > 0 else 1
        if self.shift_is_down(event):
            self.canvas.xview_scroll(direction * 3, "units")
        else:
            self.canvas.yview_scroll(direction * 3, "units")
        return "break"

    def on_linux_wheel_up(self, event):
        if self.control_is_down(event):
            self.zoom_at_event(event, 1.12)
        elif self.shift_is_down(event):
            self.canvas.xview_scroll(-3, "units")
        else:
            self.canvas.yview_scroll(-3, "units")
        return "break"

    def on_linux_wheel_down(self, event):
        if self.control_is_down(event):
            self.zoom_at_event(event, 1 / 1.12)
        elif self.shift_is_down(event):
            self.canvas.xview_scroll(3, "units")
        else:
            self.canvas.yview_scroll(3, "units")
        return "break"

    def scaled_spacing(self) -> float:
        return self.spacing * self.zoom

    def scaled_margin(self) -> float:
        return self.margin * self.zoom

    def scaled_hole_radius(self) -> float:
        return max(2.0, self.hole_radius * self.zoom)

    def grid_to_xy(self, row: int, col: int) -> Tuple[float, float]:
        spacing = self.scaled_spacing()
        margin = self.scaled_margin()
        return margin + col * spacing, margin + row * spacing

    def xy_to_grid(self, x: int, y: int) -> Optional[Tuple[int, int]]:
        spacing = self.scaled_spacing()
        margin = self.scaled_margin()
        col = round((x - margin) / spacing)
        row = round((y - margin) / spacing)
        if 0 <= row < self.rows and 0 <= col < self.cols:
            hx, hy = self.grid_to_xy(row, col)
            if abs(x - hx) <= spacing * 0.45 and abs(y - hy) <= spacing * 0.45:
                return row, col
        return None

    def board_bounds(self) -> Tuple[float, float, float, float]:
        spacing = self.scaled_spacing()
        margin = self.scaled_margin()
        edge = 18 * self.zoom
        x1 = margin - edge
        y1 = margin - edge
        x2 = margin + (self.cols - 1) * spacing + edge
        y2 = margin + (self.rows - 1) * spacing + edge
        return x1, y1, x2, y2

    def update_zoom_label(self):
        if hasattr(self, "zoom_label"):
            self.zoom_label.set(f"{round(self.zoom * 100)}%")

    def zoom_step(self, factor: float):
        self.zoom_at_screen_point(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2, factor)

    def zoom_at_event(self, event, factor: float):
        self.zoom_at_screen_point(event.x, event.y, factor)

    def zoom_at_screen_point(self, screen_x: int, screen_y: int, factor: float):
        old_zoom = self.zoom
        new_zoom = max(self.min_zoom, min(self.max_zoom, self.zoom * factor))
        if abs(new_zoom - old_zoom) < 0.0001:
            return

        # The drawing coordinates scale linearly from the canvas origin, so this
        # keeps the point under the mouse/canvas-center stable while zooming.
        old_canvas_x = self.canvas.canvasx(screen_x)
        old_canvas_y = self.canvas.canvasy(screen_y)
        scale_factor = new_zoom / old_zoom

        self.zoom = new_zoom
        self.update_zoom_label()
        self.redraw()
        self.update_idletasks()

        bbox = self.canvas.bbox("all")
        if not bbox:
            return
        x1, y1, x2, y2 = bbox
        width = max(1, x2 - x1)
        height = max(1, y2 - y1)
        desired_left = old_canvas_x * scale_factor - screen_x
        desired_top = old_canvas_y * scale_factor - screen_y
        self.canvas.xview_moveto(max(0.0, min(1.0, (desired_left - x1) / width)))
        self.canvas.yview_moveto(max(0.0, min(1.0, (desired_top - y1) / height)))
        self.status.set(f"Zoom {round(self.zoom * 100)}%")

    def zoom_in_key(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        self.zoom_step(1.15)
        return "break"

    def zoom_out_key(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        self.zoom_step(1 / 1.15)
        return "break"

    def reset_zoom(self):
        self.zoom_at_screen_point(self.canvas.winfo_width() // 2, self.canvas.winfo_height() // 2, 1 / self.zoom)

    def reset_zoom_key(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        self.reset_zoom()
        return "break"

    def redraw(self):
        self._update_mode_ui()
        self.canvas.delete("all")
        self.draw_board()
        self.draw_wires()
        self.draw_components()
        self.draw_temp_wire()
        bbox = self.canvas.bbox("all")
        if bbox:
            self.canvas.configure(scrollregion=bbox)

    def draw_board(self):
        x1, y1, x2, y2 = self.board_bounds()
        mode_color = self._mode_style()["color"]
        self.canvas.create_rectangle(x1, y1, x2, y2, fill="#117a35", outline=mode_color, width=5)
        self.canvas.create_rectangle(x1 + 4, y1 + 4, x2 - 4, y2 - 4, outline="#0b5f29", width=2)

        # Header strips similar to common prototype boards.
        strip_w = 6 * self.zoom
        strip_gap_top_a = 28 * self.zoom
        strip_gap_top_b = 16 * self.zoom
        for col in range(0, self.cols, 2):
            x, y_top = self.grid_to_xy(0, col)
            _, y_bottom = self.grid_to_xy(self.rows - 1, col)
            self.canvas.create_rectangle(x - strip_w, y_top - strip_gap_top_a, x + strip_w, y_top - strip_gap_top_b, fill="#d8d8d8", outline="")
            self.canvas.create_rectangle(x - strip_w, y_bottom + strip_gap_top_b, x + strip_w, y_bottom + strip_gap_top_a, fill="#d8d8d8", outline="")

        r = self.scaled_hole_radius()
        for row in range(self.rows):
            for col in range(self.cols):
                x, y = self.grid_to_xy(row, col)
                self.canvas.create_oval(
                    x - r,
                    y - r,
                    x + r,
                    y + r,
                    fill="#e8e8e8",
                    outline="#7a7a7a",
                    width=1,
                    tags=("hole", f"hole:{row}:{col}"),
                )

    def draw_components(self):
        for i, comp in enumerate(self.components):
            x1, y1 = self.grid_to_xy(comp.row, comp.col)
            x2, y2 = self.grid_to_xy(comp.row + comp.height - 1, comp.col + comp.width - 1)
            pad = self.scaled_spacing() * 0.38
            selected = self.selected_kind == "component" and self.selected_index == i
            outline = "#ffffff" if selected else "#111111"
            width = max(1, round(3 * self.zoom)) if selected else max(1, round(1 * self.zoom))
            self.canvas.create_rectangle(
                x1 - pad,
                y1 - pad,
                x2 + pad,
                y2 + pad,
                fill=comp.color,
                outline=outline,
                width=width,
                tags=("component", f"component:{i}"),
            )
            self.canvas.create_text(
                (x1 + x2) / 2,
                (y1 + y2) / 2,
                text=comp.name,
                fill="#111111",
                font=("TkDefaultFont", max(6, round(10 * self.zoom)), "bold"),
                tags=("component", f"component:{i}"),
            )

    def draw_wires(self):
        for i, wire in enumerate(self.wires):
            points_xy = [self.grid_to_xy(row, col) for row, col in wire.points]
            selected = self.selected_kind == "wire" and self.selected_index == i
            width = max(2, round((7 if selected else 5) * self.zoom))
            outline = "#ffffff" if selected else wire.color
            if len(points_xy) >= 2:
                flat = [value for xy in points_xy for value in xy]
                self.canvas.create_line(
                    *flat,
                    fill=outline,
                    width=width + (max(1, round(2 * self.zoom)) if selected else 0),
                    capstyle=tk.ROUND,
                    joinstyle=tk.ROUND,
                    tags=("wire", f"wire:{i}"),
                )
                self.canvas.create_line(
                    *flat,
                    fill=wire.color,
                    width=width,
                    capstyle=tk.ROUND,
                    joinstyle=tk.ROUND,
                    tags=("wire", f"wire:{i}"),
                )
            for row, col in wire.points:
                x, y = self.grid_to_xy(row, col)
                r = max(3, 5 * self.zoom)
                self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=wire.color, outline="", tags=("wire", f"wire:{i}"))
            if wire.name and len(points_xy) >= 2:
                lx, ly = points_xy[len(points_xy) // 2]
                self.canvas.create_text(lx + 8 * self.zoom, ly - 10 * self.zoom, text=wire.name, anchor="w", fill="#111111", font=("TkDefaultFont", max(6, round(9 * self.zoom))), tags=("wire", f"wire:{i}"))

    def draw_temp_wire(self):
        if not self.temp_wire_points:
            return
        points_xy = [self.grid_to_xy(row, col) for row, col in self.temp_wire_points]
        if len(points_xy) == 1:
            x, y = points_xy[0]
            r = max(3, 6 * self.zoom)
            self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=self.current_wire_color.get(), outline="#ffffff", width=max(1, round(2 * self.zoom)))
        else:
            flat = [value for xy in points_xy for value in xy]
            self.canvas.create_line(*flat, fill=self.current_wire_color.get(), width=max(2, round(4 * self.zoom)), capstyle=tk.ROUND, joinstyle=tk.ROUND, dash=(max(2, round(8 * self.zoom)), max(2, round(4 * self.zoom))))
            for x, y in points_xy:
                r = max(3, 5 * self.zoom)
                self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=self.current_wire_color.get(), outline="")

    def on_click(self, event):
        self.canvas.focus_set()
        cx, cy = self.canvas_event_xy(event)
        grid = self.xy_to_grid(cx, cy)
        mode = self.mode.get()

        if mode == "component":
            if grid is None:
                return
            row, col = grid
            w = max(1, self.component_w.get())
            h = max(1, self.component_h.get())
            if row + h > self.rows or col + w > self.cols:
                self.status.set("Component does not fit there.")
                return
            self.components.append(Component(self.current_name.get() or "Part", row, col, w, h, self.current_color.get()))
            self.selected_kind = "component"
            self.selected_index = len(self.components) - 1
            self.status.set("Component added.")
            self.redraw()
            return

        if mode == "wire":
            if grid is None:
                return

            if not self.temp_wire_points:
                self.temp_wire_points.append(grid)
                self.status.set("Wire started. Click the end hole to add it. Shift+click keeps adding bend points.")
                self.redraw()
                return

            if grid != self.temp_wire_points[-1]:
                self.temp_wire_points.append(grid)

            if len(self.temp_wire_points) >= 2 and not self.shift_is_down(event):
                self.finish_temp_wire(ask_name=False)
                self.status.set("Wire added.")
                return

            self.status.set("Bend point added. Click the final hole without Shift to finish.")
            self.redraw()
            return

        if mode == "label":
            if grid is None:
                return
            text = simpledialog.askstring("Text label", "Label text:", initialvalue=self.current_name.get())
            if text:
                row, col = grid
                self.components.append(Component(text, row, col, 3, 1, "#ffffff"))
                self.status.set("Label added.")
                self.redraw()
            return

        if mode == "select":
            self.select_at(cx, cy)
            if self.selected_kind == "component" and self.selected_index is not None and grid is not None:
                comp = self.components[self.selected_index]
                self.drag_start_grid = grid
                self.drag_component_original = (comp.row, comp.col)
            else:
                self.drag_start_grid = None
                self.drag_component_original = None
            self.redraw()

    def on_drag(self, event):
        if self.mode.get() != "select":
            return
        if self.selected_kind != "component" or self.selected_index is None:
            return
        if self.drag_start_grid is None or self.drag_component_original is None:
            return
        cx, cy = self.canvas_event_xy(event)
        grid = self.xy_to_grid(cx, cy)
        if grid is None:
            return
        start_row, start_col = self.drag_start_grid
        orig_row, orig_col = self.drag_component_original
        row, col = grid
        comp = self.components[self.selected_index]
        new_row = max(0, min(self.rows - comp.height, orig_row + row - start_row))
        new_col = max(0, min(self.cols - comp.width, orig_col + col - start_col))
        if (comp.row, comp.col) != (new_row, new_col):
            comp.row, comp.col = new_row, new_col
            self.redraw()

    def on_release(self, event):
        self.drag_start_grid = None
        self.drag_component_original = None

    def on_motion(self, event):
        cx, cy = self.canvas_event_xy(event)
        grid = self.xy_to_grid(cx, cy)
        if grid:
            row, col = grid
            if self.mode.get() == "wire" and self.temp_wire_points:
                self.status.set(f"Wire target row {row + 1}, col {col + 1}. Click to finish, Shift+click for bend, Esc to cancel.")
            else:
                self.status.set(f"Hole row {row + 1}, col {col + 1}")
        else:
            self.status.set(self._mode_style()["hint"])

    def select_at(self, x: int, y: int):
        self.selected_kind = None
        self.selected_index = None

        # Topmost component first.
        for i in range(len(self.components) - 1, -1, -1):
            comp = self.components[i]
            x1, y1 = self.grid_to_xy(comp.row, comp.col)
            x2, y2 = self.grid_to_xy(comp.row + comp.height - 1, comp.col + comp.width - 1)
            pad = self.scaled_spacing() * 0.5
            if min(x1, x2) - pad <= x <= max(x1, x2) + pad and min(y1, y2) - pad <= y <= max(y1, y2) + pad:
                self.selected_kind = "component"
                self.selected_index = i
                return

        # Wires next. Select if click is close to any segment.
        for i in range(len(self.wires) - 1, -1, -1):
            wire = self.wires[i]
            pts = [self.grid_to_xy(row, col) for row, col in wire.points]
            for a, b in zip(pts, pts[1:]):
                if self.distance_to_segment(x, y, a[0], a[1], b[0], b[1]) <= max(6, 8 * self.zoom):
                    self.selected_kind = "wire"
                    self.selected_index = i
                    return

    @staticmethod
    def distance_to_segment(px, py, ax, ay, bx, by) -> float:
        dx = bx - ax
        dy = by - ay
        if dx == 0 and dy == 0:
            return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        cx = ax + t * dx
        cy = ay + t * dy
        return ((px - cx) ** 2 + (py - cy) ** 2) ** 0.5

    def choose_component_color(self):
        color = colorchooser.askcolor(color=self.current_color.get(), title="Choose component color")
        if color and color[1]:
            self.current_color.set(color[1])

    def choose_wire_color(self):
        color = colorchooser.askcolor(color=self.current_wire_color.get(), title="Choose wire color")
        if color and color[1]:
            self.current_wire_color.set(color[1])

    def finish_temp_wire(self, event=None, ask_name: bool = False):
        if event is not None and self.event_from_text_input(event):
            return
        if len(self.temp_wire_points) < 2:
            self.cancel_temp_wire()
            return
        name = ""
        if ask_name:
            name = simpledialog.askstring("Wire name", "Wire name:", initialvalue="") or ""
        self.wires.append(Wire(name, list(self.temp_wire_points), self.current_wire_color.get()))
        self.temp_wire_points.clear()
        self.selected_kind = "wire"
        self.selected_index = len(self.wires) - 1
        self.redraw()

    def cancel_temp_wire(self, event=None):
        if self.temp_wire_points:
            self.temp_wire_points.clear()
            self.status.set("Wire cancelled.")
            self.redraw()

    def delete_selected(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        if self.selected_kind == "component" and self.selected_index is not None:
            del self.components[self.selected_index]
        elif self.selected_kind == "wire" and self.selected_index is not None:
            del self.wires[self.selected_index]
        self.selected_kind = None
        self.selected_index = None
        self.redraw()

    def resize_board(self):
        rows = simpledialog.askinteger("Rows", "Number of rows:", initialvalue=self.rows, minvalue=5, maxvalue=100)
        if rows is None:
            return
        cols = simpledialog.askinteger("Columns", "Number of columns:", initialvalue=self.cols, minvalue=5, maxvalue=150)
        if cols is None:
            return
        self.rows = rows
        self.cols = cols
        self.redraw()

    def clear_board(self):
        if messagebox.askyesno("Clear board", "Remove all components and wires?"):
            self.components.clear()
            self.wires.clear()
            self.selected_kind = None
            self.selected_index = None
            self.redraw()

    def new_file(self):
        if messagebox.askyesno("New file", "Start a new layout?"):
            self.components.clear()
            self.wires.clear()
            self.rows = 30
            self.cols = 45
            self.selected_kind = None
            self.selected_index = None
            self.redraw()

    def save_file(self):
        path = filedialog.asksaveasfilename(
            title="Save layout",
            defaultextension=".json",
            filetypes=[("JSON layout", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        data = {
            "version": 1,
            "board": {"rows": self.rows, "cols": self.cols, "spacing": self.spacing},
            "components": [asdict(c) for c in self.components],
            "wires": [{"name": w.name, "points": w.points, "color": w.color} for w in self.wires],
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        self.status.set(f"Saved {path}")

    def open_file(self):
        path = filedialog.askopenfilename(
            title="Open layout",
            filetypes=[("JSON layout", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            board = data.get("board", {})
            self.rows = int(board.get("rows", self.rows))
            self.cols = int(board.get("cols", self.cols))
            self.spacing = int(board.get("spacing", self.spacing))
            self.update_zoom_label()
            self.components = [Component(**c) for c in data.get("components", [])]
            self.wires = [Wire(w.get("name", ""), [tuple(p) for p in w.get("points", [])], w.get("color", "#d00000")) for w in data.get("wires", [])]
            self.selected_kind = None
            self.selected_index = None
            self.redraw()
        except Exception as exc:
            messagebox.showerror("Open failed", str(exc))

    def export_png(self):
        path = filedialog.asksaveasfilename(
            title="Export image",
            defaultextension=".png",
            filetypes=[("PNG image", "*.png")],
        )
        if not path:
            return

        # Tkinter can export PostScript natively. Pillow converts it to PNG if installed.
        ps_path = path + ".ps"
        x1, y1, x2, y2 = self.board_bounds()
        self.canvas.postscript(file=ps_path, colormode="color", x=x1 - 20, y=y1 - 20, width=(x2 - x1) + 40, height=(y2 - y1) + 40)
        try:
            from PIL import Image
            img = Image.open(ps_path)
            img.save(path, "png")
            self.status.set(f"Exported {path}")
        except Exception as exc:
            messagebox.showwarning(
                "PNG export needs Pillow/Ghostscript",
                "Saved a PostScript file instead.\n\n"
                f"File: {ps_path}\n\n"
                "To export PNG, install Pillow and Ghostscript.\n"
                f"Details: {exc}",
            )


if __name__ == "__main__":
    app = PerfboardPlanner()
    app.mainloop()
