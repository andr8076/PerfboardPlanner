import json
import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Tuple, Dict, Any


@dataclass
class ComponentPin:
    name: str
    row: int
    col: int


@dataclass
class ComponentJumper:
    pin_a: str
    pin_b: str
    color: str = "#00aaff"


@dataclass
class Component:
    name: str
    row: int
    col: int
    width: int
    height: int
    color: str
    side: str = "front"
    pins: List[ComponentPin] = field(default_factory=list)
    jumpers: List[ComponentJumper] = field(default_factory=list)


@dataclass
class Wire:
    name: str
    points: List[Tuple[int, int]]  # [(row, col), ...]
    color: str
    side: str = "front"
    layer: str = "main"
    lane: int = 0  # visual parallel offset; saved layout still snaps to real holes


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

        # Dual-sided board support. New components and wires are created on
        # current_side. The other side can be drawn as a ghost/see-through
        # layer so holes, pins, and components still line up physically.
        self.current_side = tk.StringVar(value="front")
        self.show_opposite_layer = tk.BooleanVar(value=True)      # opposite-side component bodies
        self.show_opposite_pins = tk.BooleanVar(value=True)       # opposite-side component pins / connection points
        self.show_opposite_wires = tk.BooleanVar(value=True)      # opposite-side wires

        self.mode = tk.StringVar(value="select")
        self.current_color = tk.StringVar(value="#ffcc66")
        self.current_wire_color = tk.StringVar(value="#d00000")
        self.current_wire_lane = tk.IntVar(value=0)
        # Wire layers used to be Main/Aux. That turned out to be the wrong
        # model: wires are now a normal editable item on each board side, and
        # the opposite-side wires have their own ghost visibility toggle.
        self.current_name = tk.StringVar(value="Part")
        self.component_w = tk.IntVar(value=4)
        self.component_h = tk.IntVar(value=2)
        self.component_pin_template: List[ComponentPin] = self.default_pins_for_size(4, 2)
        self.component_jumper_template: List[ComponentJumper] = []
        self.component_clipboard: Optional[Component] = None
        self.component_paste_count = 0
        self.component_w.trace_add("write", lambda *_: self.update_pin_count_label())
        self.component_h.trace_add("write", lambda *_: self.update_pin_count_label())

        self.selected_kind: Optional[str] = None
        self.selected_index: Optional[int] = None
        self.temp_wire_points: List[Tuple[int, int]] = []
        self.preview_line_id: Optional[int] = None
        # Calculated on every redraw. Used only for visual separation of
        # overlapping wire runs; the saved wire points remain on real holes.
        self._wire_unit_lanes: Dict[Tuple[str, int, Tuple[Tuple[int, int], Tuple[int, int]]], int] = {}


        self.mode_styles = {
            "select": {
                "label": "SELECT / MOVE",
                "color": "#20639b",
                "hint": "Click an item to select it. Drag components to move them. Double-click a component to edit it. Delete/Backspace removes the selected item.",
            },
            "component": {
                "label": "ADD COMPONENT",
                "color": "#c47f00",
                "hint": "Click a hole to place the current component size and color.",
            },
            "wire": {
                "label": "DRAW WIRE",
                "color": "#b00020",
                "hint": "Click a hole or component pin, then click the end point. Shift+click adds bend points. Right-click or Enter finishes the current wire. Only the overlapping parts of wires are separated automatically. A small hop marker means crossing without connection; a small solder dot means shared-hole connection.",
            },
            "label": {
                "label": "TEXT LABEL",
                "color": "#6a1b9a",
                "hint": "Click a hole to place a text label.",
            },
        }

        self._build_ui()
        self.redraw()

    @staticmethod
    def default_pins_for_size(width: int, height: int) -> List[ComponentPin]:
        width = max(1, int(width))
        height = max(1, int(height))
        if width == 1 and height == 1:
            return [ComponentPin("P1", 0, 0)]
        return [ComponentPin("P1", 0, 0), ComponentPin("P2", height - 1, width - 1)]

    @staticmethod
    def copy_pins(pins: List[ComponentPin]) -> List[ComponentPin]:
        return [ComponentPin(pin.name, int(pin.row), int(pin.col)) for pin in pins]

    @staticmethod
    def copy_jumpers(jumpers: List[ComponentJumper]) -> List[ComponentJumper]:
        return [ComponentJumper(j.pin_a, j.pin_b, getattr(j, "color", "#00aaff") or "#00aaff") for j in jumpers]

    @classmethod
    def clone_component(
        cls,
        comp: Component,
        row: Optional[int] = None,
        col: Optional[int] = None,
        name: Optional[str] = None,
        side: Optional[str] = None,
    ) -> Component:
        return Component(
            name=comp.name if name is None else name,
            row=comp.row if row is None else int(row),
            col=comp.col if col is None else int(col),
            width=int(comp.width),
            height=int(comp.height),
            color=comp.color,
            side=comp.side if side is None else side,
            pins=cls.copy_pins(comp.pins),
            jumpers=cls.copy_jumpers(comp.jumpers),
        )

    @staticmethod
    def normalized_pins(pins: List[ComponentPin], width: int, height: int) -> List[ComponentPin]:
        normalized: List[ComponentPin] = []
        used: set[Tuple[int, int]] = set()
        outside_limit = 20
        for pin in pins:
            row = int(pin.row)
            col = int(pin.col)
            # Pins are allowed to sit outside the component body. That covers
            # parts with legs/leads coming out of the package, headers offset
            # from the visible body, and odd custom footprints.
            if not (-outside_limit <= row < height + outside_limit and -outside_limit <= col < width + outside_limit):
                continue
            if (row, col) in used:
                continue
            name = str(pin.name or f"P{len(normalized) + 1}").strip() or f"P{len(normalized) + 1}"
            normalized.append(ComponentPin(name, row, col))
            used.add((row, col))
        return normalized

    @staticmethod
    def normalized_jumpers(jumpers: List[ComponentJumper], pins: List[ComponentPin]) -> List[ComponentJumper]:
        names = {pin.name for pin in pins}
        normalized: List[ComponentJumper] = []
        used: set[Tuple[str, str]] = set()
        for jumper in jumpers:
            a = str(jumper.pin_a or "").strip()
            b = str(jumper.pin_b or "").strip()
            if not a or not b or a == b or a not in names or b not in names:
                continue
            key = tuple(sorted((a, b)))
            if key in used:
                continue
            normalized.append(ComponentJumper(a, b, getattr(jumper, "color", "#00aaff") or "#00aaff"))
            used.add(key)
        return normalized

    def update_pin_count_label(self):
        if not hasattr(self, "pin_count_label"):
            return
        try:
            w = max(1, int(self.component_w.get()))
            h = max(1, int(self.component_h.get()))
        except Exception:
            w, h = 1, 1
        pins = self.normalized_pins(self.component_pin_template, w, h)
        self.pin_count_label.set(f"Pins: {len(pins)} for new components")

    @staticmethod
    def safe_bind(widget, sequence: str, callback):
        try:
            widget.bind(sequence, callback)
        except tk.TclError:
            # Some Tk builds do not recognize platform-specific modifiers such
            # as Command. Ignoring those keeps the app portable.
            pass

    def _build_ui(self):
        # A draggable pane divider lets the sidebar be resized instead of
        # forcing every control into one fixed-width column.
        main_pane = tk.PanedWindow(
            self,
            orient=tk.HORIZONTAL,
            sashwidth=7,
            sashrelief=tk.RAISED,
            bg="#d0d0d0",
            bd=0,
        )
        main_pane.pack(fill=tk.BOTH, expand=True)

        # The left panel uses a scrollable tab area for secondary controls, plus
        # a fixed bottom quickbar for modes and layers.
        side_outer = ttk.Frame(main_pane, width=340)
        side_outer.grid_rowconfigure(0, weight=1)
        side_outer.grid_columnconfigure(0, weight=1)
        main_pane.add(side_outer, minsize=260)

        side_scroll_area = ttk.Frame(side_outer)
        side_scroll_area.grid(row=0, column=0, sticky="nsew")
        side_scroll_area.rowconfigure(0, weight=1)
        side_scroll_area.columnconfigure(0, weight=1)

        side_canvas = tk.Canvas(side_scroll_area, borderwidth=0, highlightthickness=0, width=320)
        side_canvas.grid(row=0, column=0, sticky="nsew")
        side_bar = ttk.Scrollbar(side_scroll_area, orient=tk.VERTICAL, command=side_canvas.yview)
        side_bar.grid(row=0, column=1, sticky="ns")
        side_canvas.configure(yscrollcommand=side_bar.set)

        side = ttk.Frame(side_canvas, padding=8)
        side_window = side_canvas.create_window((0, 0), window=side, anchor="nw")

        def update_sidebar_scrollregion(event=None):
            side_canvas.configure(scrollregion=side_canvas.bbox("all"))
            side_canvas.itemconfigure(side_window, width=side_canvas.winfo_width())

        side.bind("<Configure>", update_sidebar_scrollregion)
        side_canvas.bind("<Configure>", update_sidebar_scrollregion)

        def sidebar_wheel(event):
            if getattr(event, "num", None) == 4:
                side_canvas.yview_scroll(-3, "units")
            elif getattr(event, "num", None) == 5:
                side_canvas.yview_scroll(3, "units")
            else:
                delta = -1 if event.delta > 0 else 1
                side_canvas.yview_scroll(delta * 3, "units")
            return "break"

        def bind_sidebar_wheel(event=None):
            side_canvas.bind_all("<MouseWheel>", sidebar_wheel)
            side_canvas.bind_all("<Button-4>", sidebar_wheel)
            side_canvas.bind_all("<Button-5>", sidebar_wheel)

        def unbind_sidebar_wheel(event=None):
            side_canvas.unbind_all("<MouseWheel>")
            side_canvas.unbind_all("<Button-4>")
            side_canvas.unbind_all("<Button-5>")

        side_canvas.bind("<Enter>", bind_sidebar_wheel)
        side_canvas.bind("<Leave>", unbind_sidebar_wheel)
        side.bind("<Enter>", bind_sidebar_wheel)
        side.bind("<Leave>", unbind_sidebar_wheel)

        title_row = ttk.Frame(side)
        title_row.pack(fill=tk.X)
        ttk.Label(title_row, text="Perfboard Planner", font=("TkDefaultFont", 12, "bold")).pack(side=tk.LEFT, anchor="w")

        self.sidebar_notebook = ttk.Notebook(side)
        self.sidebar_notebook.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        tool_tab = ttk.Frame(self.sidebar_notebook, padding=8)
        part_tab = ttk.Frame(self.sidebar_notebook, padding=8)
        wire_tab = ttk.Frame(self.sidebar_notebook, padding=8)
        edit_tab = ttk.Frame(self.sidebar_notebook, padding=8)
        view_tab = ttk.Frame(self.sidebar_notebook, padding=8)
        file_tab = ttk.Frame(self.sidebar_notebook, padding=8)
        self.tool_tab = tool_tab
        self.part_tab = part_tab
        self.wire_tab = wire_tab
        self.edit_tab = edit_tab
        self.view_tab = view_tab
        self.file_tab = file_tab
        self.sidebar_notebook.add(tool_tab, text="Tool")
        # The Part tab is inserted automatically only while Part mode is active.
        self.sidebar_notebook.add(edit_tab, text="Edit")
        self.sidebar_notebook.add(view_tab, text="View")
        self.sidebar_notebook.add(file_tab, text="File")

        # --- Tool tab -----------------------------------------------------
        ttk.Label(tool_tab, text="Selected item", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Button(tool_tab, text="Edit selected component", command=self.edit_selected_component).pack(fill=tk.X, pady=(4, 2))
        ttk.Button(tool_tab, text="Edit selected wire", command=self.edit_selected_wire).pack(fill=tk.X, pady=2)
        ttk.Button(tool_tab, text="Delete selected", command=self.delete_selected).pack(fill=tk.X, pady=2)
        ttk.Button(tool_tab, text="Send selected to other side", command=self.move_selected_to_other_side).pack(fill=tk.X, pady=2)

        ttk.Separator(tool_tab).pack(fill=tk.X, pady=10)
        ttk.Label(
            tool_tab,
            text=(
                "The active mode and board side are controlled from the fixed quickbar at the bottom of the sidebar.\n\n"
                "The colored banner above the board still shows what is active."
            ),
            justify=tk.LEFT,
            wraplength=270,
        ).pack(anchor="w")

        # --- Part tab -----------------------------------------------------
        ttk.Label(part_tab, text="New component", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Label(part_tab, text="Name").pack(anchor="w", pady=(5, 0))
        ttk.Entry(part_tab, textvariable=self.current_name, width=18).pack(anchor="w", fill=tk.X)

        size_row = ttk.Frame(part_tab)
        size_row.pack(anchor="w", pady=6)
        ttk.Label(size_row, text="W").pack(side=tk.LEFT)
        ttk.Spinbox(size_row, from_=1, to=30, textvariable=self.component_w, width=4).pack(side=tk.LEFT, padx=(3, 8))
        ttk.Label(size_row, text="H").pack(side=tk.LEFT)
        ttk.Spinbox(size_row, from_=1, to=30, textvariable=self.component_h, width=4).pack(side=tk.LEFT, padx=3)

        ttk.Button(part_tab, text="Component color", command=self.choose_component_color).pack(fill=tk.X, pady=2)

        ttk.Separator(part_tab).pack(fill=tk.X, pady=10)
        ttk.Label(part_tab, text="Attachment pins", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        self.pin_count_label = tk.StringVar(value="Pins: 2")
        ttk.Label(part_tab, textvariable=self.pin_count_label).pack(anchor="w", pady=(4, 2))
        ttk.Button(part_tab, text="Edit new-component pins", command=self.edit_new_component_pins).pack(fill=tk.X, pady=2)
        ttk.Button(part_tab, text="Edit selected pins", command=self.edit_selected_component_pins).pack(fill=tk.X, pady=2)

        # --- Wire tab -----------------------------------------------------
        ttk.Label(wire_tab, text="New wires", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Button(wire_tab, text="Wire color", command=self.choose_wire_color).pack(fill=tk.X, pady=(5, 2))
        ttk.Label(
            wire_tab,
            text=(
                "Overlapping wire runs are separated automatically when drawn. "
                "Crossings are marked: a solid dot means a real shared junction; "
                "a small bridge means the wires pass over/under each other without connecting."
            ),
            justify=tk.LEFT,
            wraplength=270,
        ).pack(anchor="w", pady=(8, 0))

        ttk.Separator(wire_tab).pack(fill=tk.X, pady=10)
        ttk.Label(wire_tab, text="Selected wire", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Button(wire_tab, text="Edit selected wire…", command=self.edit_selected_wire).pack(fill=tk.X, pady=(5, 2))
        ttk.Button(wire_tab, text="Apply current color", command=self.apply_current_wire_color_to_selected).pack(fill=tk.X, pady=2)
        ttk.Label(
            wire_tab,
            text="Tip: double-click a wire in Select mode to edit its name, side, and color.",
            justify=tk.LEFT,
            wraplength=270,
        ).pack(anchor="w", pady=(8, 0))

        # --- Edit tab -----------------------------------------------------
        ttk.Label(edit_tab, text="Clipboard", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        clip_row_a = ttk.Frame(edit_tab)
        clip_row_a.pack(anchor="w", fill=tk.X, pady=(5, 0))
        ttk.Button(clip_row_a, text="Copy", command=self.copy_selected_component).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(clip_row_a, text="Cut", command=self.cut_selected_component).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))
        clip_row_b = ttk.Frame(edit_tab)
        clip_row_b.pack(anchor="w", fill=tk.X, pady=(4, 0))
        ttk.Button(clip_row_b, text="Paste", command=self.paste_component).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(clip_row_b, text="Duplicate", command=self.duplicate_selected_component).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        ttk.Separator(edit_tab).pack(fill=tk.X, pady=10)
        ttk.Label(
            edit_tab,
            text=(
                "Keyboard shortcuts:\n"
                "Ctrl/Cmd+C = copy\n"
                "Ctrl/Cmd+X = cut\n"
                "Ctrl/Cmd+V = paste\n"
                "Ctrl/Cmd+D = duplicate\n"
                "Delete/Backspace = delete"
            ),
            justify=tk.LEFT,
            wraplength=270,
        ).pack(anchor="w")

        # --- View tab -----------------------------------------------------
        ttk.Label(view_tab, text="Zoom", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        zoom_row = ttk.Frame(view_tab)
        zoom_row.pack(anchor="w", pady=(5, 4), fill=tk.X)
        ttk.Button(zoom_row, text="−", width=3, command=lambda: self.zoom_step(1 / 1.15)).pack(side=tk.LEFT)
        self.zoom_label = tk.StringVar(value="100%")
        ttk.Label(zoom_row, textvariable=self.zoom_label, width=7, anchor="center").pack(side=tk.LEFT, padx=4)
        ttk.Button(zoom_row, text="+", width=3, command=lambda: self.zoom_step(1.15)).pack(side=tk.LEFT)
        ttk.Button(zoom_row, text="Reset", command=self.reset_zoom).pack(side=tk.LEFT, padx=(5, 0))

        ttk.Separator(view_tab).pack(fill=tk.X, pady=10)
        ttk.Label(view_tab, text="Board", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        board_row = ttk.Frame(view_tab)
        board_row.pack(anchor="w", pady=5, fill=tk.X)
        ttk.Button(board_row, text="Resize", command=self.resize_board).pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Button(board_row, text="Clear", command=self.clear_board).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        ttk.Separator(view_tab).pack(fill=tk.X, pady=10)
        ttk.Label(view_tab, text="Other side ghost layers", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ghost_visible_row = ttk.Frame(view_tab)
        ghost_visible_row.pack(anchor="w", fill=tk.X, pady=(5, 0))
        for text_label, var in [("Parts", self.show_opposite_layer), ("Pins", self.show_opposite_pins), ("Wires", self.show_opposite_wires)]:
            ttk.Checkbutton(
                ghost_visible_row,
                text=text_label,
                variable=var,
                command=self.toggle_layer_display,
                style="Toolbutton",
                width=8,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))
        ttk.Label(
            view_tab,
            text="These only control the see-through view of the opposite side. They do not create extra wire categories.",
            justify=tk.LEFT,
            wraplength=270,
        ).pack(anchor="w", pady=(6, 0))

        # --- File tab -----------------------------------------------------
        ttk.Label(file_tab, text="File", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        ttk.Button(file_tab, text="New", command=self.new_file).pack(fill=tk.X, pady=(5, 2))
        ttk.Button(file_tab, text="Open JSON", command=self.open_file).pack(fill=tk.X, pady=2)
        ttk.Button(file_tab, text="Save JSON", command=self.save_file).pack(fill=tk.X, pady=2)
        ttk.Button(file_tab, text="Export PNG", command=self.export_png).pack(fill=tk.X, pady=2)

        ttk.Separator(file_tab).pack(fill=tk.X, pady=10)
        ttk.Label(file_tab, text="Controls", font=("TkDefaultFont", 11, "bold")).pack(anchor="w")
        help_text = (
            "View:\n"
            "Ctrl+wheel = zoom\n"
            "+ / - = zoom in/out\n"
            "0 = reset zoom\n"
            "middle-drag = pan\n"
            "double-click component = edit component\n"
            "double-click wire = edit wire\n"
            "right-drag = pan, except while finishing a wire\n"
            "mouse wheel = vertical scroll\n"
            "Shift+wheel = horizontal scroll\n\n"
            "Wire mode:\n"
            "click start, click end = add wire\n"
            "Shift+click = add bend point\n"
            "overlaps are separated automatically\n"
            "right-click / Enter = finish wire\n"
            "Esc = cancel wire"
        )
        ttk.Label(file_tab, text=help_text, justify=tk.LEFT, wraplength=270).pack(anchor="w", pady=4)

        quickbar = ttk.Frame(side_outer, padding=(8, 6))
        quickbar.grid(row=3, column=0, sticky="ew")
        quickbar.columnconfigure(0, weight=1)

        ttk.Label(quickbar, text="Mode", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        mode_tabs = ttk.Frame(quickbar)
        mode_tabs.pack(fill=tk.X, pady=(2, 6))
        for text_label, value in [("Select", "select"), ("Part", "component"), ("Wire", "wire"), ("Text", "label")]:
            ttk.Radiobutton(
                mode_tabs,
                text=text_label,
                variable=self.mode,
                value=value,
                command=self._mode_changed,
                style="Toolbutton",
                width=7,
            ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 2))

        ttk.Label(quickbar, text="Layers", font=("TkDefaultFont", 9, "bold")).pack(anchor="w")
        layer_tabs = ttk.Frame(quickbar)
        layer_tabs.pack(fill=tk.X, pady=(2, 0))

        # Five controls share the row evenly. This keeps the ghost-wire control
        # at 1/5 of the available width instead of forcing the row wider than
        # the sidebar on small windows.
        for column in range(5):
            layer_tabs.columnconfigure(column, weight=1, uniform="layer_tabs")

        layer_buttons = [
            ttk.Radiobutton(
                layer_tabs,
                text="Front",
                variable=self.current_side,
                value="front",
                command=self._side_changed,
                style="Toolbutton",
                width=5,
            ),
            ttk.Radiobutton(
                layer_tabs,
                text="Back",
                variable=self.current_side,
                value="back",
                command=self._side_changed,
                style="Toolbutton",
                width=5,
            ),
            ttk.Checkbutton(
                layer_tabs,
                text="Parts",
                variable=self.show_opposite_layer,
                command=self.toggle_layer_display,
                style="Toolbutton",
                width=5,
            ),
            ttk.Checkbutton(
                layer_tabs,
                text="Pins",
                variable=self.show_opposite_pins,
                command=self.toggle_layer_display,
                style="Toolbutton",
                width=5,
            ),
            ttk.Checkbutton(
                layer_tabs,
                text="Wire",
                variable=self.show_opposite_wires,
                command=self.toggle_layer_display,
                style="Toolbutton",
                width=5,
            ),
        ]
        for column, button in enumerate(layer_buttons):
            button.grid(row=0, column=column, sticky="ew", padx=(0, 2 if column < len(layer_buttons) - 1 else 0))

        self.status = tk.StringVar(value="Ready")
        ttk.Label(side_outer, textvariable=self.status, wraplength=315, padding=8).grid(row=1, column=0, sticky="ew")
        ttk.Separator(side_outer).grid(row=2, column=0, sticky="ew")

        board_area = ttk.Frame(main_pane)
        main_pane.add(board_area, minsize=380)
        self.after(50, lambda: main_pane.sash_place(0, 340, 0))

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
        self.canvas.bind("<Double-Button-1>", self.on_double_click)
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
        self.canvas.bind("<Control-c>", self.copy_selected_component)
        self.canvas.bind("<Control-C>", self.copy_selected_component)
        self.canvas.bind("<Control-x>", self.cut_selected_component)
        self.canvas.bind("<Control-X>", self.cut_selected_component)
        self.canvas.bind("<Control-v>", self.paste_component)
        self.canvas.bind("<Control-V>", self.paste_component)
        self.canvas.bind("<Control-d>", self.duplicate_selected_component)
        self.canvas.bind("<Control-D>", self.duplicate_selected_component)
        self.safe_bind(self.canvas, "<Command-c>", self.copy_selected_component)
        self.safe_bind(self.canvas, "<Command-C>", self.copy_selected_component)
        self.safe_bind(self.canvas, "<Command-x>", self.cut_selected_component)
        self.safe_bind(self.canvas, "<Command-X>", self.cut_selected_component)
        self.safe_bind(self.canvas, "<Command-v>", self.paste_component)
        self.safe_bind(self.canvas, "<Command-V>", self.paste_component)
        self.safe_bind(self.canvas, "<Command-d>", self.duplicate_selected_component)
        self.safe_bind(self.canvas, "<Command-D>", self.duplicate_selected_component)
        self.canvas.bind("<Escape>", self.cancel_temp_wire)
        self.canvas.bind("<Return>", lambda event: self.finish_temp_wire(event, ask_name=True))
        self.bind("<Delete>", self.delete_selected)
        self.bind("<BackSpace>", self.delete_selected)
        self.bind("<Control-c>", self.copy_selected_component)
        self.bind("<Control-C>", self.copy_selected_component)
        self.bind("<Control-x>", self.cut_selected_component)
        self.bind("<Control-X>", self.cut_selected_component)
        self.bind("<Control-v>", self.paste_component)
        self.bind("<Control-V>", self.paste_component)
        self.bind("<Control-d>", self.duplicate_selected_component)
        self.bind("<Control-D>", self.duplicate_selected_component)
        self.safe_bind(self, "<Command-c>", self.copy_selected_component)
        self.safe_bind(self, "<Command-C>", self.copy_selected_component)
        self.safe_bind(self, "<Command-x>", self.cut_selected_component)
        self.safe_bind(self, "<Command-X>", self.cut_selected_component)
        self.safe_bind(self, "<Command-v>", self.paste_component)
        self.safe_bind(self, "<Command-V>", self.paste_component)
        self.safe_bind(self, "<Command-d>", self.duplicate_selected_component)
        self.safe_bind(self, "<Command-D>", self.duplicate_selected_component)
        self.bind("<Escape>", self.cancel_temp_wire)
        self.bind("<Return>", lambda event: self.finish_temp_wire(event, ask_name=True))
        self.bind("<Key-plus>", self.zoom_in_key)
        self.bind("<Key-equal>", self.zoom_in_key)
        self.bind("<Key-minus>", self.zoom_out_key)
        self.bind("<Key-0>", self.reset_zoom_key)

        self.drag_start_grid: Optional[Tuple[int, int]] = None
        self.drag_component_original: Optional[Tuple[int, int]] = None
        self.update_zoom_label()
        self.update_pin_count_label()
        self._update_mode_ui()

    def edit_selected_component(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        if self.selected_kind != "component" or self.selected_index is None:
            self.status.set("Select a component first, then double-click it or use Edit selected component.")
            return "break"
        self.open_component_editor(self.selected_index)
        return "break"

    def edit_selected_wire(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        if self.selected_kind != "wire" or self.selected_index is None:
            self.status.set("Select a wire first, then double-click it or use Edit selected wire.")
            return "break"
        self.open_wire_editor(self.selected_index)
        return "break"

    def open_wire_editor(self, wire_index: int):
        if not (0 <= wire_index < len(self.wires)):
            self.status.set("The selected wire no longer exists.")
            return

        wire = self.wires[wire_index]
        win = tk.Toplevel(self)
        win.title("Edit wire")
        win.transient(self)
        win.resizable(False, False)

        name_var = tk.StringVar(value=wire.name)
        color_var = tk.StringVar(value=wire.color)
        side_var = tk.StringVar(value=wire.side)

        body = ttk.Frame(win, padding=10)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text="Wire", font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(body, text="Name").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(body, textvariable=name_var, width=24).grid(row=1, column=1, columnspan=2, sticky="ew", pady=(8, 2))

        ttk.Label(body, text="Side").grid(row=2, column=0, sticky="w", pady=2)
        side_frame = ttk.Frame(body)
        side_frame.grid(row=2, column=1, columnspan=2, sticky="w", pady=2)
        ttk.Radiobutton(side_frame, text="Front", variable=side_var, value="front").pack(side=tk.LEFT)
        ttk.Radiobutton(side_frame, text="Back", variable=side_var, value="back").pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(body, text="Color").grid(row=3, column=0, sticky="w", pady=2)
        color_preview = tk.Label(body, textvariable=color_var, bg=color_var.get(), fg="#111111", width=12, relief=tk.SUNKEN)
        color_preview.grid(row=3, column=1, sticky="w", pady=2)

        def choose_color():
            color = colorchooser.askcolor(color=color_var.get(), title="Choose wire color", parent=win)
            if color and color[1]:
                color_var.set(color[1])
                color_preview.configure(bg=color[1])

        ttk.Button(body, text="Choose…", command=choose_color).grid(row=3, column=2, sticky="ew", padx=(6, 0), pady=2)

        points_text = " → ".join(f"R{row + 1}C{col + 1}" for row, col in wire.points)
        ttk.Label(body, text="Points").grid(row=4, column=0, sticky="nw", pady=(8, 2))
        ttk.Label(body, text=points_text or "No points", wraplength=280, justify=tk.LEFT).grid(row=4, column=1, columnspan=2, sticky="w", pady=(8, 2))

        ttk.Label(
            body,
            text="Overlapping runs and crossing markers are handled automatically on the board view.",
            wraplength=280,
            justify=tk.LEFT,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 2))

        def apply_changes():
            if not (0 <= wire_index < len(self.wires)):
                win.destroy()
                self.status.set("The selected wire no longer exists.")
                return
            edited = self.wires[wire_index]
            edited.name = name_var.get().strip()
            edited.color = color_var.get() or "#d00000"
            edited.side = side_var.get() if side_var.get() in {"front", "back"} else self.current_side.get()
            edited.layer = "main"
            edited.lane = 0
            self.selected_kind = "wire"
            self.selected_index = wire_index
            self.current_side.set(edited.side)
            self.status.set("Updated wire.")
            win.destroy()
            self.redraw()

        bottom = ttk.Frame(win, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="Apply", command=apply_changes).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(bottom, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)

        win.bind("<Return>", lambda event: apply_changes())
        win.bind("<Escape>", lambda event: win.destroy())
        win.grab_set()
        win.wait_window()

    def apply_current_wire_color_to_selected(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        if self.selected_kind != "wire" or self.selected_index is None or not (0 <= self.selected_index < len(self.wires)):
            self.status.set("Select a wire first, then apply the current wire color.")
            return "break"
        wire = self.wires[self.selected_index]
        wire.color = self.current_wire_color.get()
        wire.layer = "main"
        self.status.set("Applied current wire color to selected wire.")
        self.redraw()
        return "break"

    def apply_current_wire_lane_to_selected(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        if self.selected_kind != "wire" or self.selected_index is None or not (0 <= self.selected_index < len(self.wires)):
            self.status.set("Select a wire first, then apply the current lane.")
            return "break"
        wire = self.wires[self.selected_index]
        wire.lane = self.current_wire_lane_value()
        self.status.set(f"Applied lane {wire.lane} to selected wire.")
        self.redraw()
        return "break"

    def wire_unit_segments(self, wire: Wire) -> set:
        # Break horizontal/vertical segments into single-hole spans. This lets
        # auto-stagger detect partial overlaps, not only wires with identical
        # endpoints. Diagonal/custom segments fall back to their whole segment.
        result = set()
        pts = [(int(r), int(c)) for r, c in wire.points]
        for (r1, c1), (r2, c2) in zip(pts, pts[1:]):
            if r1 == r2 and c1 != c2:
                step = 1 if c2 > c1 else -1
                for c in range(c1, c2, step):
                    a = (r1, c)
                    b = (r1, c + step)
                    result.add(tuple(sorted((a, b))))
            elif c1 == c2 and r1 != r2:
                step = 1 if r2 > r1 else -1
                for r in range(r1, r2, step):
                    a = (r, c1)
                    b = (r + step, c1)
                    result.add(tuple(sorted((a, b))))
            else:
                result.add(tuple(sorted(((r1, c1), (r2, c2)))))
        return result

    def auto_stagger_overlapping_wires(self, event=None):
        side = self.current_side.get()
        indexed = [(i, self.wires[i]) for i in range(len(self.wires)) if self.wires[i].side == side]
        if len(indexed) < 2:
            self.status.set("Need at least two wires on this side to auto-stagger.")
            return "break"

        segment_sets = {i: self.wire_unit_segments(wire) for i, wire in indexed}
        conflicts = {i: set() for i, _ in indexed}
        for pos, (i, _) in enumerate(indexed):
            for j, _ in indexed[pos + 1:]:
                if segment_sets[i] and segment_sets[i].intersection(segment_sets[j]):
                    conflicts[i].add(j)
                    conflicts[j].add(i)

        active_conflicts = {i: neighbours for i, neighbours in conflicts.items() if neighbours}
        if not active_conflicts:
            self.status.set("No overlapping wire runs found on this side.")
            return "break"

        lane_choices = [0, 1, -1, 2, -2, 3, -3, 4, -4]
        for i in sorted(active_conflicts, key=lambda idx: len(active_conflicts[idx]), reverse=True):
            used = {self.wire_lane_value(self.wires[j]) for j in active_conflicts[i] if hasattr(self.wires[j], "lane")}
            for lane in lane_choices:
                if lane not in used:
                    self.wires[i].lane = lane
                    break

        self.status.set(f"Auto-staggered {len(active_conflicts)} overlapping wire(s) on the {self.current_side_label()} side.")
        self.redraw()
        return "break"

    def open_component_editor(self, component_index: int):
        if not (0 <= component_index < len(self.components)):
            self.status.set("The selected component no longer exists.")
            return

        comp = self.components[component_index]
        win = tk.Toplevel(self)
        win.title(f"Edit component: {comp.name}")
        win.transient(self)
        win.resizable(False, False)

        name_var = tk.StringVar(value=comp.name)
        row_var = tk.IntVar(value=comp.row + 1)
        col_var = tk.IntVar(value=comp.col + 1)
        width_var = tk.IntVar(value=comp.width)
        height_var = tk.IntVar(value=comp.height)
        color_var = tk.StringVar(value=comp.color)
        side_var = tk.StringVar(value=comp.side)
        edit_pins: List[ComponentPin] = self.copy_pins(comp.pins)
        edit_jumpers: List[ComponentJumper] = self.copy_jumpers(comp.jumpers)
        pin_summary = tk.StringVar(value="")

        body = ttk.Frame(win, padding=10)
        body.pack(fill=tk.BOTH, expand=True)

        ttk.Label(body, text="Component", font=("TkDefaultFont", 11, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")

        ttk.Label(body, text="Name").grid(row=1, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(body, textvariable=name_var, width=24).grid(row=1, column=1, columnspan=2, sticky="ew", pady=(8, 2))

        ttk.Label(body, text="Position").grid(row=2, column=0, sticky="w", pady=2)
        pos_frame = ttk.Frame(body)
        pos_frame.grid(row=2, column=1, columnspan=2, sticky="w", pady=2)
        ttk.Label(pos_frame, text="Row").pack(side=tk.LEFT)
        ttk.Spinbox(pos_frame, from_=1, to=max(1, self.rows), textvariable=row_var, width=5).pack(side=tk.LEFT, padx=(3, 10))
        ttk.Label(pos_frame, text="Col").pack(side=tk.LEFT)
        ttk.Spinbox(pos_frame, from_=1, to=max(1, self.cols), textvariable=col_var, width=5).pack(side=tk.LEFT, padx=(3, 0))

        ttk.Label(body, text="Size").grid(row=3, column=0, sticky="w", pady=2)
        size_frame = ttk.Frame(body)
        size_frame.grid(row=3, column=1, columnspan=2, sticky="w", pady=2)
        ttk.Label(size_frame, text="W").pack(side=tk.LEFT)
        ttk.Spinbox(size_frame, from_=1, to=max(1, self.cols), textvariable=width_var, width=5).pack(side=tk.LEFT, padx=(3, 10))
        ttk.Label(size_frame, text="H").pack(side=tk.LEFT)
        ttk.Spinbox(size_frame, from_=1, to=max(1, self.rows), textvariable=height_var, width=5).pack(side=tk.LEFT, padx=(3, 0))

        ttk.Label(body, text="Side").grid(row=4, column=0, sticky="w", pady=2)
        side_frame = ttk.Frame(body)
        side_frame.grid(row=4, column=1, columnspan=2, sticky="w", pady=2)
        ttk.Radiobutton(side_frame, text="Front", variable=side_var, value="front").pack(side=tk.LEFT)
        ttk.Radiobutton(side_frame, text="Back", variable=side_var, value="back").pack(side=tk.LEFT, padx=(12, 0))

        ttk.Label(body, text="Color").grid(row=5, column=0, sticky="w", pady=2)
        color_preview = tk.Label(body, textvariable=color_var, bg=color_var.get(), fg="#111111", width=12, relief=tk.SUNKEN)
        color_preview.grid(row=5, column=1, sticky="w", pady=2)

        def choose_color():
            color = colorchooser.askcolor(color=color_var.get(), title="Choose component color", parent=win)
            if color and color[1]:
                color_var.set(color[1])
                color_preview.configure(bg=color[1])

        ttk.Button(body, text="Choose…", command=choose_color).grid(row=5, column=2, sticky="ew", padx=(6, 0), pady=2)

        ttk.Separator(body).grid(row=6, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Label(body, text="Attachment pins", font=("TkDefaultFont", 11, "bold")).grid(row=7, column=0, columnspan=3, sticky="w")
        ttk.Label(body, textvariable=pin_summary).grid(row=8, column=0, columnspan=3, sticky="w", pady=(4, 2))

        def read_size() -> Tuple[int, int]:
            try:
                width = max(1, int(width_var.get()))
                height = max(1, int(height_var.get()))
            except Exception:
                width, height = max(1, comp.width), max(1, comp.height)
            return width, height

        def update_pin_summary(*_):
            width, height = read_size()
            kept = self.normalized_pins(edit_pins, width, height)
            kept_jumpers = self.normalized_jumpers(edit_jumpers, kept)
            removed = len(edit_pins) - len(kept)
            extra = f" ({removed} far-outside pins will be removed)" if removed else ""
            pin_summary.set(f"Pins: {len(kept)}, internal jumpers: {len(kept_jumpers)}{extra}")

        def edit_pins_action():
            nonlocal edit_pins
            width, height = read_size()
            pins = self.normalized_pins(edit_pins, width, height)

            def apply_pins(updated_pins: List[ComponentPin], updated_jumpers: Optional[List[ComponentJumper]] = None):
                nonlocal edit_pins, edit_jumpers
                edit_pins = self.normalized_pins(updated_pins, width, height)
                edit_jumpers = self.normalized_jumpers(updated_jumpers or [], edit_pins)
                update_pin_summary()

            self.open_pin_editor(f"Pin layout: {name_var.get() or comp.name}", width, height, pins, apply_pins, jumpers=edit_jumpers)

        ttk.Button(body, text="Edit pins…", command=edit_pins_action).grid(row=9, column=0, columnspan=3, sticky="ew", pady=(2, 0))

        for variable in (width_var, height_var):
            variable.trace_add("write", update_pin_summary)
        update_pin_summary()

        def apply_changes():
            if not (0 <= component_index < len(self.components)):
                win.destroy()
                self.status.set("The selected component no longer exists.")
                return
            edited = self.components[component_index]
            try:
                width = max(1, int(width_var.get()))
                height = max(1, int(height_var.get()))
                row = max(0, int(row_var.get()) - 1)
                col = max(0, int(col_var.get()) - 1)
            except Exception:
                messagebox.showerror("Invalid values", "Row, column, width, and height must be numbers.", parent=win)
                return

            if width > self.cols or height > self.rows:
                messagebox.showerror("Component too large", "The component must fit inside the current board size.", parent=win)
                return

            row = max(0, min(self.rows - height, row))
            col = max(0, min(self.cols - width, col))
            side = side_var.get() if side_var.get() in {"front", "back"} else self.current_side.get()

            edited.name = name_var.get().strip() or "Part"
            edited.row = row
            edited.col = col
            edited.width = width
            edited.height = height
            edited.color = color_var.get() or "#ffcc66"
            edited.side = side
            edited.pins = self.normalized_pins(edit_pins, width, height)
            edited.jumpers = self.normalized_jumpers(edit_jumpers, edited.pins)

            self.selected_kind = "component"
            self.selected_index = component_index
            self.current_side.set(side)
            self.status.set(f"Updated component {edited.name}.")
            win.destroy()
            self.redraw()

        bottom = ttk.Frame(win, padding=(10, 0, 10, 10))
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="Apply", command=apply_changes).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(bottom, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)

        win.bind("<Return>", lambda event: apply_changes())
        win.bind("<Escape>", lambda event: win.destroy())
        win.grab_set()
        win.wait_window()

    def edit_new_component_pins(self):
        w = max(1, int(self.component_w.get()))
        h = max(1, int(self.component_h.get()))
        pins = self.normalized_pins(self.component_pin_template, w, h)
        if not pins:
            pins = self.default_pins_for_size(w, h)

        def apply(updated_pins: List[ComponentPin], updated_jumpers: Optional[List[ComponentJumper]] = None):
            self.component_pin_template = self.normalized_pins(updated_pins, w, h)
            self.component_jumper_template = self.normalized_jumpers(updated_jumpers or [], self.component_pin_template)
            self.update_pin_count_label()
            self.status.set("New-component pin layout updated.")

        self.open_pin_editor("Pin layout for new components", w, h, pins, apply, jumpers=self.component_jumper_template)

    def edit_selected_component_pins(self):
        if self.selected_kind != "component" or self.selected_index is None:
            self.status.set("Select a component first, then use Edit selected pins.")
            return
        comp = self.components[self.selected_index]
        pins = self.normalized_pins(comp.pins, comp.width, comp.height)
        if not pins:
            pins = self.default_pins_for_size(comp.width, comp.height)

        def apply(updated_pins: List[ComponentPin], updated_jumpers: Optional[List[ComponentJumper]] = None):
            comp.pins = self.normalized_pins(updated_pins, comp.width, comp.height)
            comp.jumpers = self.normalized_jumpers(updated_jumpers or [], comp.pins)
            self.status.set(f"Pins and internal jumpers updated for {comp.name}.")
            self.redraw()

        self.open_pin_editor(f"Pin layout: {comp.name}", comp.width, comp.height, pins, apply, jumpers=comp.jumpers)

    def open_pin_editor(
        self,
        title: str,
        width: int,
        height: int,
        pins: List[ComponentPin],
        on_apply,
        jumpers: Optional[List[ComponentJumper]] = None,
    ):
        win = tk.Toplevel(self)
        win.title(title)
        win.transient(self)
        win.resizable(False, False)

        info = ttk.Label(
            win,
            text=(
                "Click cells to toggle component attachment pins.\n"
                "Rows/columns are relative to the component's top-left hole.\n"
                "Cells outside the yellow body area are external leads/pins."
            ),
            justify=tk.LEFT,
            padding=8,
        )
        info.pack(anchor="w")

        editor = ttk.Frame(win, padding=8)
        editor.pack(fill=tk.BOTH, expand=True)

        external_margin = 2
        min_row = -external_margin
        max_row = height + external_margin - 1
        min_col = -external_margin
        max_col = width + external_margin - 1
        total_rows = max_row - min_row + 1
        total_cols = max_col - min_col + 1

        cell = 42
        left = 28
        top = 28
        canvas_size_x = max(220, total_cols * cell + 56)
        canvas_size_y = max(170, total_rows * cell + 56)
        pin_canvas = tk.Canvas(
            editor,
            width=canvas_size_x,
            height=canvas_size_y,
            bg="#f7f7f7",
            highlightthickness=1,
            highlightbackground="#999999",
        )
        pin_canvas.grid(row=0, column=0, rowspan=12, sticky="nsew", padx=(0, 10))

        pin_map: Dict[Tuple[int, int], str] = {
            (int(pin.row), int(pin.col)): pin.name
            for pin in self.normalized_pins(pins, width, height)
        }
        jumper_list: List[ComponentJumper] = self.normalized_jumpers(jumpers or [], [ComponentPin(name, row, col) for (row, col), name in pin_map.items()])
        selected_pos: List[Optional[Tuple[int, int]]] = [None]

        ttk.Label(editor, text="Pins", font=("TkDefaultFont", 10, "bold")).grid(row=0, column=1, sticky="w")
        pin_list = tk.Listbox(editor, width=26, height=8, exportselection=False)
        pin_list.grid(row=1, column=1, sticky="nsew")

        ttk.Label(editor, text="Internal jumpers", font=("TkDefaultFont", 10, "bold")).grid(row=6, column=1, sticky="w", pady=(10, 0))
        jumper_box = tk.Listbox(editor, width=26, height=5, exportselection=False)
        jumper_box.grid(row=7, column=1, sticky="nsew")

        jumper_a = tk.StringVar(value="")
        jumper_b = tk.StringVar(value="")
        jumper_select_row = ttk.Frame(editor)
        jumper_select_row.grid(row=8, column=1, sticky="ew", pady=(5, 2))
        jumper_combo_a = ttk.Combobox(jumper_select_row, textvariable=jumper_a, state="readonly", width=9)
        jumper_combo_a.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        jumper_combo_b = ttk.Combobox(jumper_select_row, textvariable=jumper_b, state="readonly", width=9)
        jumper_combo_b.pack(side=tk.LEFT, fill=tk.X, expand=True)

        def cell_xy(row: int, col: int) -> Tuple[int, int]:
            return left + (col - min_col) * cell, top + (row - min_row) * cell

        def pins_sorted() -> List[Tuple[Tuple[int, int], str]]:
            return sorted(pin_map.items(), key=lambda item: (item[0][0], item[0][1], item[1]))

        def current_pin_objects() -> List[ComponentPin]:
            return [ComponentPin(name, row, col) for (row, col), name in pins_sorted()]

        def pin_name_to_pos() -> Dict[str, Tuple[int, int]]:
            result: Dict[str, Tuple[int, int]] = {}
            for (row, col), name in pins_sorted():
                result.setdefault(name, (row, col))
            return result

        def refresh_jumpers():
            nonlocal jumper_list
            jumper_list = self.normalized_jumpers(jumper_list, current_pin_objects())

        def next_pin_name() -> str:
            existing = set(pin_map.values())
            idx = 1
            while f"P{idx}" in existing:
                idx += 1
            return f"P{idx}"

        def draw_editor():
            refresh_jumpers()
            pin_canvas.delete("all")
            pin_list.delete(0, tk.END)
            jumper_box.delete(0, tk.END)

            name_to_pos = pin_name_to_pos()

            for row in range(min_row, max_row + 1):
                for col in range(min_col, max_col + 1):
                    x, y = cell_xy(row, col)
                    is_selected = selected_pos[0] == (row, col)
                    is_body = 0 <= row < height and 0 <= col < width
                    has_pin = (row, col) in pin_map
                    fill = "#fff1c7" if is_body else "#e8edf4"
                    if has_pin:
                        fill = "#ffe08a"
                    outline = "#20639b" if is_selected else ("#999999" if is_body else "#c0c6cf")
                    outline_w = 3 if is_selected else 1
                    pin_canvas.create_rectangle(x - 16, y - 16, x + 16, y + 16, fill=fill, outline=outline, width=outline_w)
                    pin_canvas.create_oval(x - 4, y - 4, x + 4, y + 4, fill="#777777", outline="")
                    pin_canvas.create_text(x, y + 22, text=f"{row},{col}", fill="#555555", font=("TkDefaultFont", 7))

            for jumper in jumper_list:
                if jumper.pin_a not in name_to_pos or jumper.pin_b not in name_to_pos:
                    continue
                ax, ay = cell_xy(*name_to_pos[jumper.pin_a])
                bx, by = cell_xy(*name_to_pos[jumper.pin_b])
                pin_canvas.create_line(ax, ay, bx, by, fill=jumper.color, width=5, capstyle=tk.ROUND)
                pin_canvas.create_line(ax, ay, bx, by, fill="#ffffff", width=1, capstyle=tk.ROUND)

            for (row, col), name in pins_sorted():
                x, y = cell_xy(row, col)
                pin_canvas.create_oval(x - 11, y - 11, x + 11, y + 11, fill="#111111", outline="#ffffff", width=2)
                pin_canvas.create_text(x, y, text=name, fill="#ffffff", font=("TkDefaultFont", 8, "bold"))
                pin_list.insert(tk.END, f"{name}: row {row}, col {col}")

            pin_names = [name for _, name in pins_sorted()]
            jumper_combo_a.configure(values=pin_names)
            jumper_combo_b.configure(values=pin_names)
            if jumper_a.get() not in pin_names:
                jumper_a.set(pin_names[0] if pin_names else "")
            if jumper_b.get() not in pin_names:
                jumper_b.set(pin_names[1] if len(pin_names) > 1 else (pin_names[0] if pin_names else ""))

            for jumper in jumper_list:
                jumper_box.insert(tk.END, f"{jumper.pin_a} ↔ {jumper.pin_b}")

        def canvas_to_cell(x: int, y: int) -> Optional[Tuple[int, int]]:
            col = round((x - left) / cell + min_col)
            row = round((y - top) / cell + min_row)
            if min_row <= row <= max_row and min_col <= col <= max_col:
                cx, cy = cell_xy(row, col)
                if abs(x - cx) <= 20 and abs(y - cy) <= 20:
                    return row, col
            return None

        def on_editor_click(event):
            pos = canvas_to_cell(event.x, event.y)
            if pos is None:
                return
            selected_pos[0] = pos
            if pos in pin_map:
                old_name = pin_map[pos]
                del pin_map[pos]
                jumper_list[:] = [j for j in jumper_list if j.pin_a != old_name and j.pin_b != old_name]
            else:
                pin_map[pos] = next_pin_name()
            draw_editor()

        def on_list_select(event=None):
            sel = pin_list.curselection()
            sorted_items = pins_sorted()
            if not sel or sel[0] >= len(sorted_items):
                return
            selected_pos[0] = sorted_items[sel[0]][0]
            draw_editor()
            pin_list.selection_set(sel[0])

        def rename_selected():
            pos = selected_pos[0]
            if pos is None or pos not in pin_map:
                messagebox.showinfo("Rename pin", "Select or create a pin first.", parent=win)
                return
            old_name = pin_map[pos]
            new_name = simpledialog.askstring("Rename pin", "Pin name:", initialvalue=old_name, parent=win)
            if new_name is None:
                return
            new_name = new_name.strip()
            if not new_name:
                return
            pin_map[pos] = new_name
            for jumper in jumper_list:
                if jumper.pin_a == old_name:
                    jumper.pin_a = new_name
                if jumper.pin_b == old_name:
                    jumper.pin_b = new_name
            draw_editor()

        def clear_pins():
            pin_map.clear()
            jumper_list.clear()
            selected_pos[0] = None
            draw_editor()

        def set_two_pin_horizontal():
            pin_map.clear()
            jumper_list.clear()
            pin_map[(0, 0)] = "P1"
            pin_map[(0, width - 1)] = "P2"
            selected_pos[0] = None
            draw_editor()

        def set_four_corners():
            pin_map.clear()
            jumper_list.clear()
            coords = [(0, 0), (0, width - 1), (height - 1, 0), (height - 1, width - 1)]
            for idx, pos in enumerate(dict.fromkeys(coords), start=1):
                pin_map[pos] = f"P{idx}"
            selected_pos[0] = None
            draw_editor()

        def set_dip_sides():
            pin_map.clear()
            jumper_list.clear()
            idx = 1
            for row in range(height):
                pin_map[(row, 0)] = f"P{idx}"
                idx += 1
            if width > 1:
                for row in range(height - 1, -1, -1):
                    pin_map[(row, width - 1)] = f"P{idx}"
                    idx += 1
            selected_pos[0] = None
            draw_editor()

        def set_external_leads():
            pin_map.clear()
            jumper_list.clear()
            pin_map[(height // 2, -1)] = "IN"
            pin_map[(height // 2, width)] = "OUT"
            selected_pos[0] = None
            draw_editor()

        def add_jumper():
            a = jumper_a.get()
            b = jumper_b.get()
            if not a or not b or a == b:
                messagebox.showinfo("Internal jumper", "Choose two different pins.", parent=win)
                return
            key = tuple(sorted((a, b)))
            existing = {tuple(sorted((j.pin_a, j.pin_b))) for j in jumper_list}
            if key not in existing:
                jumper_list.append(ComponentJumper(a, b))
            draw_editor()

        def remove_selected_jumper():
            sel = jumper_box.curselection()
            if not sel:
                return
            index = sel[0]
            if 0 <= index < len(jumper_list):
                del jumper_list[index]
            draw_editor()

        def apply_and_close():
            updated_pins = current_pin_objects()
            updated_jumpers = self.normalized_jumpers(jumper_list, updated_pins)
            try:
                on_apply(updated_pins, updated_jumpers)
            except TypeError:
                on_apply(updated_pins)
            win.destroy()

        pin_canvas.bind("<Button-1>", on_editor_click)
        pin_list.bind("<<ListboxSelect>>", on_list_select)

        ttk.Button(editor, text="Rename selected", command=rename_selected).grid(row=2, column=1, sticky="ew", pady=(8, 2))
        ttk.Button(editor, text="Clear pins", command=clear_pins).grid(row=3, column=1, sticky="ew", pady=2)
        ttk.Separator(editor).grid(row=4, column=1, sticky="ew", pady=6)
        ttk.Button(editor, text="2-pin horizontal", command=set_two_pin_horizontal).grid(row=5, column=1, sticky="ew", pady=2)
        ttk.Button(editor, text="4 corners", command=set_four_corners).grid(row=9, column=1, sticky="ew", pady=(8, 2))
        ttk.Button(editor, text="DIP sides", command=set_dip_sides).grid(row=10, column=1, sticky="ew", pady=2)
        ttk.Button(editor, text="External leads", command=set_external_leads).grid(row=11, column=1, sticky="ew", pady=2)
        ttk.Button(editor, text="Add jumper", command=add_jumper).grid(row=12, column=1, sticky="ew", pady=(8, 2))
        ttk.Button(editor, text="Remove selected jumper", command=remove_selected_jumper).grid(row=13, column=1, sticky="ew", pady=2)

        bottom = ttk.Frame(win, padding=8)
        bottom.pack(fill=tk.X)
        ttk.Button(bottom, text="Apply", command=apply_and_close).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(bottom, text="Cancel", command=win.destroy).pack(side=tk.RIGHT)

        draw_editor()
        win.grab_set()
        win.wait_window()

    def _mode_changed(self):
        self.cancel_temp_wire()
        self.selected_kind = None
        self.selected_index = None
        self._update_mode_ui()
        self.status.set(self._mode_style()["hint"])
        self.redraw()

    def _side_changed(self):
        self.cancel_temp_wire()
        self.selected_kind = None
        self.selected_index = None
        self._update_mode_ui()
        self.status.set(f"Viewing {self.current_side_label()} side. New items are placed on this side.")
        self.redraw()

    def toggle_layer_display(self):
        self._update_mode_ui()
        self.redraw()

    def current_side_label(self) -> str:
        return "Front" if self.current_side.get() == "front" else "Back"

    def other_side(self) -> str:
        return "back" if self.current_side.get() == "front" else "front"

    @staticmethod
    def side_label(side: str) -> str:
        return "Front" if side == "front" else "Back"

    def wire_layer_visible(self, layer: str) -> bool:
        # Compatibility with older JSON files that may contain a legacy
        # Main/Aux layer value. Layers no longer hide active-side wires.
        return True

    @staticmethod
    def wire_layer_label(layer: str) -> str:
        return "Wire"

    @staticmethod
    def _parse_hex_color(color: str) -> Tuple[int, int, int]:
        color = (color or "#000000").strip()
        if color.startswith("#"):
            color = color[1:]
        if len(color) == 3:
            color = "".join(ch * 2 for ch in color)
        if len(color) != 6:
            return 0, 0, 0
        try:
            return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)
        except ValueError:
            return 0, 0, 0

    @classmethod
    def blend_hex_color(cls, foreground: str, background: str = "#117a35", opacity: float = 0.5) -> str:
        # Tk canvas items do not support true alpha transparency. This blends
        # the ghost color toward the board color, and the stipple pattern below
        # lets the active side still show through visually.
        opacity = max(0.0, min(1.0, float(opacity)))
        fr, fg, fb = cls._parse_hex_color(foreground)
        br, bg, bb = cls._parse_hex_color(background)
        r = round(fr * opacity + br * (1.0 - opacity))
        g = round(fg * opacity + bg * (1.0 - opacity))
        b = round(fb * opacity + bb * (1.0 - opacity))
        return f"#{r:02x}{g:02x}{b:02x}"

    def update_part_tab_visibility(self):
        if not hasattr(self, "sidebar_notebook") or not hasattr(self, "part_tab"):
            return

        def tab_present(tab) -> bool:
            return str(tab) in self.sidebar_notebook.tabs()

        def remove_tab(tab):
            if tab_present(tab):
                was_selected = self.sidebar_notebook.select() == str(tab)
                self.sidebar_notebook.forget(tab)
                if was_selected and hasattr(self, "tool_tab"):
                    self.sidebar_notebook.select(self.tool_tab)

        mode = self.mode.get()

        if mode == "component":
            if not tab_present(self.part_tab):
                self.sidebar_notebook.insert(1, self.part_tab, text="Part")
            self.sidebar_notebook.select(self.part_tab)
        else:
            remove_tab(self.part_tab)

        if hasattr(self, "wire_tab"):
            if mode == "wire":
                if not tab_present(self.wire_tab):
                    self.sidebar_notebook.insert(1, self.wire_tab, text="Wire")
                self.sidebar_notebook.select(self.wire_tab)
            else:
                remove_tab(self.wire_tab)

    def _mode_style(self) -> Dict[str, str]:
        return self.mode_styles.get(self.mode.get(), self.mode_styles["select"])

    def _update_mode_ui(self):
        if hasattr(self, "sidebar_notebook"):
            self.update_part_tab_visibility()
        if not hasattr(self, "mode_banner"):
            return
        style = self._mode_style()
        side = self.current_side_label().upper()
        visible_ghosts = []
        if self.show_opposite_layer.get():
            visible_ghosts.append("parts")
        if self.show_opposite_pins.get():
            visible_ghosts.append("pins")
        if self.show_opposite_wires.get():
            visible_ghosts.append("wires")
        ghost = " | ghost: " + ", ".join(visible_ghosts) if visible_ghosts else " | ghost off"
        self.mode_banner.configure(text=f"{side} SIDE  |  {style['label']}  —  {style['hint']}{ghost}", bg=style["color"])
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
        self.recompute_auto_wire_spacing()
        self.draw_board()

        # Draw the non-active side first as a ghost layer. It is visible but not
        # editable/selectable while this side is active.
        opposite = self.other_side()
        if self.show_opposite_layer.get():
            self.draw_components(side=opposite, ghost=True)
        if self.show_opposite_wires.get():
            self.draw_wires(side=opposite, ghost=True)
        if self.show_opposite_pins.get():
            self.draw_opposite_connection_points(opposite)

        active = self.current_side.get()
        self.draw_wires(side=active, ghost=False)
        self.draw_wire_connection_markers(side=active)
        self.draw_components(side=active, ghost=False)
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

    def draw_components(self, side: Optional[str] = None, ghost: bool = False):
        for i, comp in enumerate(self.components):
            if side is not None and comp.side != side:
                continue
            x1, y1 = self.grid_to_xy(comp.row, comp.col)
            x2, y2 = self.grid_to_xy(comp.row + comp.height - 1, comp.col + comp.width - 1)
            pad = self.scaled_spacing() * 0.38
            selected = (not ghost) and self.selected_kind == "component" and self.selected_index == i

            if ghost:
                outline = self.blend_hex_color(comp.color, "#f2f2f2", 0.45)
                ghost_fill = self.blend_hex_color(comp.color, "#117a35", 0.50)
                width = max(1, round(1 * self.zoom))
                tags = ("ghost_component", f"ghost_component:{i}")
                self.canvas.create_rectangle(
                    x1 - pad,
                    y1 - pad,
                    x2 + pad,
                    y2 + pad,
                    fill=ghost_fill,
                    outline=outline,
                    width=width,
                    stipple="gray50",
                    tags=tags,
                )
                self.canvas.create_text(
                    (x1 + x2) / 2,
                    (y1 + y2) / 2,
                    text=f"{comp.name} ({self.side_label(comp.side)})",
                    fill="#555555",
                    font=("TkDefaultFont", max(6, round(8 * self.zoom)), "bold"),
                    tags=tags,
                )
                if self.show_opposite_pins.get():
                    self.draw_component_jumpers(comp, ghost=True)
                    self.draw_component_pins(i, comp, selected=False, ghost=True)
                continue

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
            self.draw_component_jumpers(comp, ghost=False)
            self.draw_component_pins(i, comp, selected, ghost=False)

    def component_pin_position_map(self, comp: Component) -> Dict[str, Tuple[float, float]]:
        result: Dict[str, Tuple[float, float]] = {}
        for pin in self.normalized_pins(comp.pins, comp.width, comp.height):
            result.setdefault(pin.name, self.grid_to_xy(comp.row + pin.row, comp.col + pin.col))
        return result

    def draw_component_jumpers(self, comp: Component, ghost: bool = False):
        pins = self.normalized_pins(comp.pins, comp.width, comp.height)
        jumpers = self.normalized_jumpers(comp.jumpers, pins)
        if not jumpers:
            return
        positions = self.component_pin_position_map(comp)
        for jumper in jumpers:
            if jumper.pin_a not in positions or jumper.pin_b not in positions:
                continue
            ax, ay = positions[jumper.pin_a]
            bx, by = positions[jumper.pin_b]
            if ghost:
                self.canvas.create_line(
                    ax,
                    ay,
                    bx,
                    by,
                    fill="#2f5f73",
                    width=max(1, round(3 * self.zoom)),
                    dash=(max(2, round(4 * self.zoom)), max(2, round(3 * self.zoom))),
                    capstyle=tk.ROUND,
                    tags=("ghost_internal_jumper",),
                )
            else:
                self.canvas.create_line(
                    ax,
                    ay,
                    bx,
                    by,
                    fill=jumper.color,
                    width=max(2, round(4 * self.zoom)),
                    capstyle=tk.ROUND,
                    tags=("internal_jumper",),
                )
                self.canvas.create_line(
                    ax,
                    ay,
                    bx,
                    by,
                    fill="#ffffff",
                    width=max(1, round(1 * self.zoom)),
                    capstyle=tk.ROUND,
                    tags=("internal_jumper",),
                )

    def draw_component_pins(self, component_index: int, comp: Component, selected: bool, ghost: bool = False):
        pins = self.normalized_pins(comp.pins, comp.width, comp.height)
        for pin_index, pin in enumerate(pins):
            x, y = self.grid_to_xy(comp.row + pin.row, comp.col + pin.col)
            r = max(3, 5 * self.zoom)
            if ghost:
                self.canvas.create_oval(
                    x - r,
                    y - r,
                    x + r,
                    y + r,
                    fill="",
                    outline="#222222",
                    width=max(1, round(2 * self.zoom)),
                    dash=(max(2, round(3 * self.zoom)), max(2, round(2 * self.zoom))),
                    tags=("ghost_pin", f"ghost_pin:{component_index}:{pin_index}"),
                )
                if self.zoom >= 1.35:
                    self.canvas.create_text(
                        x + 7 * self.zoom,
                        y - 8 * self.zoom,
                        text=f"{comp.name}.{pin.name}",
                        anchor="w",
                        fill="#444444",
                        font=("TkDefaultFont", max(6, round(7 * self.zoom)), "bold"),
                        tags=("ghost_pin", f"ghost_pin:{component_index}:{pin_index}"),
                    )
                continue

            outline = "#ffffff" if not selected else "#00d5ff"
            self.canvas.create_oval(
                x - r,
                y - r,
                x + r,
                y + r,
                fill="#111111",
                outline=outline,
                width=max(1, round(2 * self.zoom)),
                tags=("component_pin", f"component_pin:{component_index}:{pin_index}", "component", f"component:{component_index}"),
            )
            if selected or self.zoom >= 1.15:
                self.canvas.create_text(
                    x + 7 * self.zoom,
                    y - 8 * self.zoom,
                    text=pin.name,
                    anchor="w",
                    fill="#ffffff" if selected else "#111111",
                    font=("TkDefaultFont", max(6, round(8 * self.zoom)), "bold"),
                    tags=("component_pin", f"component_pin:{component_index}:{pin_index}", "component", f"component:{component_index}"),
                )


    @staticmethod
    def clamp_wire_lane(value) -> int:
        # Kept for compatibility with older JSON files. Manual lanes are no
        # longer part of the UI; v16 calculates per-segment offsets automatically.
        try:
            return max(-8, min(8, int(value)))
        except Exception:
            return 0

    def current_wire_lane_value(self) -> int:
        return 0

    @staticmethod
    def wire_lane_value(wire: Wire) -> int:
        return PerfboardPlanner.clamp_wire_lane(getattr(wire, "lane", 0))

    def wire_lane_offset(self, wire_or_lane) -> float:
        # Compatibility shim for a few older call sites. The new renderer does
        # not offset the whole wire anymore; only shared unit-runs are offset.
        return 0.0

    @staticmethod
    def offset_polyline_points(points_xy: List[Tuple[float, float]], offset: float) -> List[Tuple[float, float]]:
        # Compatibility shim. Whole-polyline offsetting caused bad bend and
        # endpoint artefacts, so v16 keeps this as a no-op.
        return list(points_xy)

    @staticmethod
    def _unit_lane_choices(count: int) -> List[int]:
        if count <= 1:
            return [0]
        # For two wires, use -1 and +1 so neither one hides the other on the
        # original centerline. For odd counts, keep one in the center.
        if count % 2:
            half = count // 2
            return [0] + [value for pair in ((n, -n) for n in range(1, half + 1)) for value in pair]
        half = count // 2
        return [value for n in range(1, half + 1) for value in (-n, n)]

    @staticmethod
    def _unit_segment_key(a: Tuple[int, int], b: Tuple[int, int]) -> Tuple[Tuple[int, int], Tuple[int, int]]:
        return tuple(sorted(((int(a[0]), int(a[1])), (int(b[0]), int(b[1])))))  # type: ignore[return-value]

    def wire_unit_entries(self, wire: Wire) -> List[Dict[str, Any]]:
        """Return ordered one-hole spans for a wire.

        Horizontal/vertical runs are split into one-hole spans so overlaps can
        be handled only where they really happen. Diagonal runs are preserved as
        one span; they still draw and select correctly, but they are not treated
        as perfboard-style parallel tracks.
        """
        entries: List[Dict[str, Any]] = []
        pts = [(int(r), int(c)) for r, c in wire.points]
        for (r1, c1), (r2, c2) in zip(pts, pts[1:]):
            if r1 == r2 and c1 != c2:
                step = 1 if c2 > c1 else -1
                for c in range(c1, c2, step):
                    a = (r1, c)
                    b = (r1, c + step)
                    entries.append({"a": a, "b": b, "key": self._unit_segment_key(a, b), "orthogonal": True})
            elif c1 == c2 and r1 != r2:
                step = 1 if r2 > r1 else -1
                for r in range(r1, r2, step):
                    a = (r, c1)
                    b = (r + step, c1)
                    entries.append({"a": a, "b": b, "key": self._unit_segment_key(a, b), "orthogonal": True})
            elif (r1, c1) != (r2, c2):
                a = (r1, c1)
                b = (r2, c2)
                entries.append({"a": a, "b": b, "key": self._unit_segment_key(a, b), "orthogonal": False})
        return entries

    def wire_unit_segments(self, wire: Wire) -> set:
        return {entry["key"] for entry in self.wire_unit_entries(wire) if entry.get("orthogonal")}

    def auto_stagger_overlapping_wires(self, event=None):
        # Manual command kept as a harmless refresh action for old shortcuts.
        self.recompute_auto_wire_spacing()
        self.status.set("Wire overlaps are handled automatically per segment.")
        self.redraw()
        return "break"

    def recompute_auto_wire_spacing(self):
        """Calculate visual-only offsets for shared unit wire runs.

        v15 offset the whole polyline. That made bends, endpoints, and nearby
        component pins drift too far. v16 offsets only the exact one-hole spans
        where more than one same-side wire uses the same path.
        """
        self._wire_unit_lanes = {}
        for wire in self.wires:
            wire.lane = 0

        for side in ("front", "back"):
            memberships: Dict[Tuple[Tuple[int, int], Tuple[int, int]], List[int]] = {}
            for i, wire in enumerate(self.wires):
                if wire.side != side:
                    continue
                seen_for_wire = set()
                for entry in self.wire_unit_entries(wire):
                    if not entry.get("orthogonal"):
                        continue
                    key = entry["key"]
                    if key in seen_for_wire:
                        continue
                    seen_for_wire.add(key)
                    memberships.setdefault(key, []).append(i)

            for key, indices in memberships.items():
                unique_indices = sorted(set(indices))
                if len(unique_indices) <= 1:
                    continue
                lanes = self._unit_lane_choices(len(unique_indices))
                for wire_index, lane in zip(unique_indices, lanes):
                    self._wire_unit_lanes[(side, wire_index, key)] = lane

    def wire_parallel_offset_px(self) -> float:
        # Wide enough that two 5px wires do not visually merge, but not so wide
        # that they look like they jumped to another row/column.
        return max(5.0, min(9.0, 0.32 * self.scaled_spacing()))

    def _offset_grid_span_xy(self, a: Tuple[int, int], b: Tuple[int, int], lane: int) -> Tuple[Tuple[float, float], Tuple[float, float]]:
        ax, ay = self.grid_to_xy(*a)
        bx, by = self.grid_to_xy(*b)
        if lane == 0:
            return (ax, ay), (bx, by)

        amount = lane * self.wire_parallel_offset_px()
        ar, ac = a
        br, bc = b
        if ar == br:
            # Horizontal board run: separate vertically.
            nx, ny = 0.0, 1.0
        elif ac == bc:
            # Vertical board run: separate horizontally.
            nx, ny = -1.0, 0.0
        else:
            # Diagonal/non-orthogonal spans are left centered.
            nx, ny = 0.0, 0.0
        return (ax + nx * amount, ay + ny * amount), (bx + nx * amount, by + ny * amount)

    @staticmethod
    def _same_xy(a: Tuple[float, float], b: Tuple[float, float], eps: float = 0.5) -> bool:
        return abs(a[0] - b[0]) <= eps and abs(a[1] - b[1]) <= eps

    def wire_visual_path_points(self, wire_index: int, wire: Wire) -> List[Tuple[float, float]]:
        if len(wire.points) < 2:
            return [self.grid_to_xy(row, col) for row, col in wire.points]

        result: List[Tuple[float, float]] = []
        side = wire.side
        for segment_start, segment_end in zip(wire.points, wire.points[1:]):
            temp_wire = Wire("", [tuple(segment_start), tuple(segment_end)], wire.color, side=wire.side)
            entries = self.wire_unit_entries(temp_wire)
            if not entries:
                continue

            actual_start_xy = self.grid_to_xy(*segment_start)
            actual_end_xy = self.grid_to_xy(*segment_end)
            if not result:
                result.append(actual_start_xy)
            elif not self._same_xy(result[-1], actual_start_xy):
                result.append(actual_start_xy)

            for entry in entries:
                key = entry["key"]
                lane = self._wire_unit_lanes.get((side, wire_index, key), 0)
                start_xy, end_xy = self._offset_grid_span_xy(entry["a"], entry["b"], lane if entry.get("orthogonal") else 0)
                if not self._same_xy(result[-1], start_xy):
                    result.append(start_xy)
                if not self._same_xy(result[-1], end_xy):
                    result.append(end_xy)

            if not self._same_xy(result[-1], actual_end_xy):
                result.append(actual_end_xy)

        return result

    def wire_visual_segments(self, wire_index: int, wire: Wire) -> List[Tuple[Tuple[float, float], Tuple[float, float], bool]]:
        path = self.wire_visual_path_points(wire_index, wire)
        result: List[Tuple[Tuple[float, float], Tuple[float, float], bool]] = []
        actual_holes = {self.grid_to_xy(row, col) for row, col in wire.points}
        for a, b in zip(path, path[1:]):
            if self._same_xy(a, b):
                continue
            # Connector doglegs are the short links from real holes into a
            # visually separated track. Crossing markers should ignore them.
            connector = (a in actual_holes) or (b in actual_holes)
            result.append((a, b, connector))
        return result

    @staticmethod
    def segment_intersection_xy(a: Tuple[float, float], b: Tuple[float, float], c: Tuple[float, float], d: Tuple[float, float]):
        ax, ay = a
        bx, by = b
        cx, cy = c
        dx, dy = d
        den = (ax - bx) * (cy - dy) - (ay - by) * (cx - dx)
        if abs(den) < 0.000001:
            return None
        t = ((ax - cx) * (cy - dy) - (ay - cy) * (cx - dx)) / den
        u = ((ax - cx) * (ay - by) - (ay - cy) * (ax - bx)) / den
        eps = 0.000001
        if -eps <= t <= 1.0 + eps and -eps <= u <= 1.0 + eps:
            x = ax + t * (bx - ax)
            y = ay + t * (by - ay)
            return x, y, t, u
        return None

    def explicit_wire_junctions(self, side: str) -> Dict[Tuple[int, int], List[int]]:
        holes: Dict[Tuple[int, int], List[int]] = {}
        for i, wire in enumerate(self.wires):
            if wire.side != side:
                continue
            for point in set((int(row), int(col)) for row, col in wire.points):
                holes.setdefault(point, []).append(i)
        return {point: indices for point, indices in holes.items() if len(indices) > 1}

    def draw_wire_connection_markers(self, side: str):
        # Solid solder dots mean a true shared hole/junction. Bridge symbols mean
        # a visual crossing only, with no electrical connection implied.
        junctions = self.explicit_wire_junctions(side)
        junction_xy = []
        for point, indices in junctions.items():
            x, y = self.grid_to_xy(*point)
            junction_xy.append((x, y))
            r = max(3, 4.3 * self.zoom)
            self.canvas.create_oval(
                x - r,
                y - r,
                x + r,
                y + r,
                fill="#ffffff",
                outline="#111111",
                width=max(1, round(1.5 * self.zoom)),
                tags=("wire_junction",),
            )
            inner = max(1.5, 2.0 * self.zoom)
            self.canvas.create_oval(
                x - inner,
                y - inner,
                x + inner,
                y + inner,
                fill="#111111",
                outline="",
                tags=("wire_junction",),
            )

        visible = [(i, self.wires[i]) for i in range(len(self.wires)) if self.wires[i].side == side]
        if len(visible) < 2:
            return

        segments = {i: self.wire_visual_segments(i, wire) for i, wire in visible}
        markers = []
        seen = set()
        skip_radius = max(5.0, 6.0 * self.zoom)
        for pos, (i, wire_a) in enumerate(visible):
            for j, wire_b in visible[pos + 1:]:
                for seg_a in segments[i]:
                    a1, a2, a_connector = seg_a
                    if a_connector:
                        continue
                    for seg_b in segments[j]:
                        b1, b2, b_connector = seg_b
                        if b_connector:
                            continue
                        hit = self.segment_intersection_xy(a1, a2, b1, b2)
                        if hit is None:
                            continue
                        x, y, t, u = hit
                        if any(((x - jx) ** 2 + (y - jy) ** 2) ** 0.5 <= skip_radius for jx, jy in junction_xy):
                            continue
                        endpoint_touch = (t < 0.05 or t > 0.95 or u < 0.05 or u > 0.95)
                        if endpoint_touch:
                            continue
                        key = (round(x, 1), round(y, 1), min(i, j), max(i, j))
                        if key in seen:
                            continue
                        seen.add(key)
                        # Later-created wire is shown as the one that hops over.
                        top_index, top_segment = (j, seg_b) if j > i else (i, seg_a)
                        markers.append((x, y, self.wires[top_index], top_segment))

        for x, y, top_wire, top_segment in markers:
            self.draw_wire_bridge_marker(x, y, top_wire, top_segment)

    def draw_wire_bridge_marker(self, x: float, y: float, wire: Wire, segment):
        (ax, ay), (bx, by), _ = segment
        horizontal = abs(bx - ax) >= abs(by - ay)
        r_x = max(7, 9 * self.zoom)
        r_y = max(5, 7 * self.zoom)
        # Draw only a small hop symbol. The old filled oval was too visually
        # aggressive and could look like a green blob over the circuit.
        if horizontal:
            bbox = (x - r_x, y - r_y, x + r_x, y + r_y)
            start = 0
        else:
            bbox = (x - r_y, y - r_x, x + r_y, y + r_x)
            start = 90
        base_width = max(3, round(5 * self.zoom))
        self.canvas.create_arc(
            *bbox,
            start=start,
            extent=180,
            style=tk.ARC,
            outline="#ffffff",
            width=base_width + max(2, round(3 * self.zoom)),
            tags=("wire_bridge",),
        )
        self.canvas.create_arc(
            *bbox,
            start=start,
            extent=180,
            style=tk.ARC,
            outline=wire.color,
            width=base_width,
            tags=("wire_bridge",),
        )

    def draw_wires(self, side: Optional[str] = None, ghost: bool = False):
        for i, wire in enumerate(self.wires):
            if side is not None and wire.side != side:
                continue
            selected = (not ghost) and self.selected_kind == "wire" and self.selected_index == i
            width = max(1, round((4 if ghost else (7 if selected else 5)) * self.zoom))
            path = self.wire_visual_path_points(i, wire)

            if len(path) >= 2:
                flat = [value for xy in path for value in xy]
                if ghost:
                    self.canvas.create_line(
                        *flat,
                        fill=wire.color,
                        width=width,
                        capstyle=tk.ROUND,
                        joinstyle=tk.ROUND,
                        dash=(max(3, round(7 * self.zoom)), max(3, round(5 * self.zoom))),
                        tags=("ghost_wire", f"ghost_wire:{i}"),
                    )
                else:
                    outline = "#ffffff" if selected else wire.color
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

            # Draw the actual snapped control/connection holes on top of the
            # routed visual path. This keeps endpoint meaning clear even when a
            # shared span is offset beside the hole centerline.
            for row, col in wire.points:
                x, y = self.grid_to_xy(row, col)
                r = max(2, (4 if ghost else 5) * self.zoom)
                if ghost:
                    self.canvas.create_oval(x - r, y - r, x + r, y + r, fill="", outline=wire.color, width=max(1, round(1 * self.zoom)), tags=("ghost_wire", f"ghost_wire:{i}"))
                else:
                    self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=wire.color, outline="", tags=("wire", f"wire:{i}"))
            if wire.name and len(path) >= 2 and not ghost:
                lx, ly = path[len(path) // 2]
                self.canvas.create_text(lx + 8 * self.zoom, ly - 10 * self.zoom, text=wire.name, anchor="w", fill="#111111", font=("TkDefaultFont", max(6, round(9 * self.zoom))), tags=("wire", f"wire:{i}"))

    def draw_opposite_connection_points(self, side: str):
        # Draw a small ring on every opposite-side component pin, even when the
        # full opposite component ghost layer is off. This makes via/solder
        # planning easier while keeping the active side uncluttered.
        seen: set[Tuple[int, int]] = set()
        for comp in self.components:
            if comp.side != side:
                continue
            for pin in self.normalized_pins(comp.pins, comp.width, comp.height):
                row, col = comp.row + pin.row, comp.col + pin.col
                if (row, col) in seen:
                    continue
                seen.add((row, col))
                x, y = self.grid_to_xy(row, col)
                r = max(5, 7 * self.zoom)
                self.canvas.create_oval(
                    x - r,
                    y - r,
                    x + r,
                    y + r,
                    fill="",
                    outline="#00d5ff",
                    width=max(1, round(2 * self.zoom)),
                    dash=(max(2, round(2 * self.zoom)), max(2, round(2 * self.zoom))),
                    tags=("opposite_connection",),
                )

    def draw_temp_wire(self):
        if not self.temp_wire_points:
            return
        points_xy = [self.grid_to_xy(row, col) for row, col in self.temp_wire_points]
        visual_xy = list(points_xy)
        if len(points_xy) == 1:
            x, y = points_xy[0]
            r = max(3, 6 * self.zoom)
            self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=self.current_wire_color.get(), outline="#ffffff", width=max(1, round(2 * self.zoom)))
        else:
            flat = [value for xy in visual_xy for value in xy]
            self.canvas.create_line(*flat, fill=self.current_wire_color.get(), width=max(2, round(4 * self.zoom)), capstyle=tk.ROUND, joinstyle=tk.ROUND, dash=(max(2, round(8 * self.zoom)), max(2, round(4 * self.zoom))))
            for x, y in points_xy:
                r = max(3, 5 * self.zoom)
                self.canvas.create_oval(x - r, y - r, x + r, y + r, fill=self.current_wire_color.get(), outline="")

    def component_index_at(self, x: float, y: float, side: Optional[str] = None) -> Optional[int]:
        side = self.current_side.get() if side is None else side
        for i in range(len(self.components) - 1, -1, -1):
            comp = self.components[i]
            if comp.side != side:
                continue
            x1, y1 = self.grid_to_xy(comp.row, comp.col)
            x2, y2 = self.grid_to_xy(comp.row + comp.height - 1, comp.col + comp.width - 1)
            pad = self.scaled_spacing() * 0.5
            if min(x1, x2) - pad <= x <= max(x1, x2) + pad and min(y1, y2) - pad <= y <= max(y1, y2) + pad:
                return i
        return None

    def wire_index_at(self, x: float, y: float, side: Optional[str] = None) -> Optional[int]:
        side = self.current_side.get() if side is None else side
        for i in range(len(self.wires) - 1, -1, -1):
            wire = self.wires[i]
            if wire.side != side:
                continue
            for a, b, _connector in self.wire_visual_segments(i, wire):
                if self.distance_to_segment(x, y, a[0], a[1], b[0], b[1]) <= max(6, 8 * self.zoom):
                    return i
        return None

    def on_double_click(self, event):
        self.canvas.focus_set()
        if self.mode.get() != "select":
            return
        cx, cy = self.canvas_event_xy(event)
        idx = self.component_index_at(cx, cy, self.current_side.get())
        if idx is not None:
            self.selected_kind = "component"
            self.selected_index = idx
            self.drag_start_grid = None
            self.drag_component_original = None
            self.redraw()
            self.open_component_editor(idx)
            return "break"

        wire_idx = self.wire_index_at(cx, cy, self.current_side.get())
        if wire_idx is not None:
            self.selected_kind = "wire"
            self.selected_index = wire_idx
            self.drag_start_grid = None
            self.drag_component_original = None
            self.redraw()
            self.open_wire_editor(wire_idx)
            return "break"

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
            pins = self.normalized_pins(self.component_pin_template, w, h)
            self.components.append(Component(
                self.current_name.get() or "Part",
                row,
                col,
                w,
                h,
                self.current_color.get(),
                side=self.current_side.get(),
                pins=self.copy_pins(pins),
                jumpers=self.normalized_jumpers(self.component_jumper_template, pins),
            ))
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
                self.components.append(Component(text, row, col, 3, 1, "#ffffff", side=self.current_side.get()))
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
            active_pin_text = self.pin_text_at_grid(grid, self.current_side.get())
            other_pin_text = self.pin_text_at_grid(grid, self.other_side(), include_side=True) if self.show_opposite_pins.get() else ""
            combined_pin_text = ", ".join(part for part in [active_pin_text, other_pin_text] if part)
            location = f"{combined_pin_text} at row {row + 1}, col {col + 1}" if combined_pin_text else f"Hole row {row + 1}, col {col + 1}"
            if self.mode.get() == "wire" and self.temp_wire_points:
                self.status.set(f"Wire target: {location}. Click to finish, Shift+click for bend, Esc to cancel.")
            elif self.mode.get() == "wire":
                self.status.set(f"Wire start: {location}")
            else:
                self.status.set(location)
        else:
            self.status.set(self._mode_style()["hint"])

    def pin_text_at_grid(self, grid: Tuple[int, int], side: Optional[str] = None, include_side: bool = False) -> str:
        row, col = grid
        hits: List[str] = []
        for comp in self.components:
            if side is not None and comp.side != side:
                continue
            for pin in self.normalized_pins(comp.pins, comp.width, comp.height):
                if comp.row + pin.row == row and comp.col + pin.col == col:
                    prefix = f"{self.side_label(comp.side)}:" if include_side else ""
                    hits.append(f"{prefix}{comp.name}.{pin.name}")
        return ", ".join(hits)

    def select_at(self, x: int, y: int):
        self.selected_kind = None
        self.selected_index = None

        # Topmost component first.
        for i in range(len(self.components) - 1, -1, -1):
            comp = self.components[i]
            if comp.side != self.current_side.get():
                continue
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
            if wire.side != self.current_side.get():
                continue
            pts = [self.grid_to_xy(row, col) for row, col in wire.points]
            visual_pts = self.offset_polyline_points(pts, self.wire_lane_offset(wire))
            for a, b in zip(visual_pts, visual_pts[1:]):
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
        self.wires.append(Wire(name, list(self.temp_wire_points), self.current_wire_color.get(), side=self.current_side.get(), lane=0))
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
            self.status.set("Component deleted.")
        elif self.selected_kind == "wire" and self.selected_index is not None:
            del self.wires[self.selected_index]
            self.status.set("Wire deleted.")
        self.selected_kind = None
        self.selected_index = None
        self.redraw()
        return "break"

    def selected_component(self) -> Optional[Component]:
        if self.selected_kind != "component" or self.selected_index is None:
            return None
        if not (0 <= self.selected_index < len(self.components)):
            return None
        comp = self.components[self.selected_index]
        if comp.side != self.current_side.get():
            return None
        return comp

    def copy_selected_component(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        comp = self.selected_component()
        if comp is None:
            self.status.set("Select a component first, then copy.")
            return "break"
        self.component_clipboard = self.clone_component(comp)
        self.component_paste_count = 0
        self.status.set(f"Copied component {comp.name}.")
        return "break"

    def cut_selected_component(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        comp = self.selected_component()
        if comp is None:
            self.status.set("Select a component first, then cut.")
            return "break"
        self.component_clipboard = self.clone_component(comp)
        self.component_paste_count = 0
        del self.components[self.selected_index]
        self.selected_kind = None
        self.selected_index = None
        self.status.set(f"Cut component {comp.name}.")
        self.redraw()
        return "break"

    def clamp_component_position(self, comp: Component, row: int, col: int) -> Tuple[int, int]:
        max_row = max(0, self.rows - comp.height)
        max_col = max(0, self.cols - comp.width)
        return max(0, min(max_row, int(row))), max(0, min(max_col, int(col)))

    def paste_component(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        if self.component_clipboard is None:
            self.status.set("No copied component to paste.")
            return "break"

        source = self.component_clipboard
        self.component_paste_count += 1
        offset = self.component_paste_count
        row, col = self.clamp_component_position(source, source.row + offset, source.col + offset)
        pasted = self.clone_component(source, row=row, col=col, side=self.current_side.get())
        self.components.append(pasted)
        self.selected_kind = "component"
        self.selected_index = len(self.components) - 1
        self.mode.set("select")
        self.status.set(f"Pasted component {pasted.name}.")
        self.redraw()
        return "break"

    def duplicate_selected_component(self, event=None):
        if event is not None and self.event_from_text_input(event):
            return
        comp = self.selected_component()
        if comp is None:
            self.status.set("Select a component first, then duplicate.")
            return "break"

        row, col = self.clamp_component_position(comp, comp.row + 1, comp.col + 1)
        duplicate = self.clone_component(comp, row=row, col=col)
        self.components.append(duplicate)
        self.selected_kind = "component"
        self.selected_index = len(self.components) - 1
        self.mode.set("select")
        self.status.set(f"Duplicated component {duplicate.name}.")
        self.redraw()
        return "break"

    def move_selected_to_other_side(self):
        target_side = self.other_side()
        if self.selected_kind == "component" and self.selected_index is not None and 0 <= self.selected_index < len(self.components):
            self.components[self.selected_index].side = target_side
            self.current_side.set(target_side)
            self._update_mode_ui()
            self.status.set(f"Moved component to {self.current_side_label()} side.")
            self.redraw()
            return
        if self.selected_kind == "wire" and self.selected_index is not None and 0 <= self.selected_index < len(self.wires):
            self.wires[self.selected_index].side = target_side
            self.current_side.set(target_side)
            self._update_mode_ui()
            self.status.set(f"Moved wire to {self.current_side_label()} side.")
            self.redraw()
            return
        self.status.set("Select a component or wire first, then send it to the other side.")

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
            "version": 7,
            "board": {"rows": self.rows, "cols": self.cols, "spacing": self.spacing},
            "components": [asdict(c) for c in self.components],
            "wires": [asdict(w) for w in self.wires],
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
            self.components = []
            for c in data.get("components", []):
                pins = [ComponentPin(pin.get("name", ""), int(pin.get("row", 0)), int(pin.get("col", 0))) for pin in c.get("pins", [])]
                jumpers = [ComponentJumper(j.get("pin_a", ""), j.get("pin_b", ""), j.get("color", "#00aaff")) for j in c.get("jumpers", [])]
                normalized_pins = self.normalized_pins(pins, int(c.get("width", 1)), int(c.get("height", 1)))
                self.components.append(Component(
                    c.get("name", "Part"),
                    int(c.get("row", 0)),
                    int(c.get("col", 0)),
                    int(c.get("width", 1)),
                    int(c.get("height", 1)),
                    c.get("color", "#ffcc66"),
                    side=c.get("side", "front"),
                    pins=normalized_pins,
                    jumpers=self.normalized_jumpers(jumpers, normalized_pins),
                ))
            self.wires = [Wire(
                w.get("name", ""),
                [tuple(p) for p in w.get("points", [])],
                w.get("color", "#d00000"),
                side=w.get("side", "front"),
                layer="main",
                lane=self.clamp_wire_lane(w.get("lane", 0)),
            ) for w in data.get("wires", [])]
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
