# Perfboard Planner

A desktop app for planning circuit layouts on perfboards. It helps you place components, manage connections, auto-route traces, and generate a bill of materials. Works on Linux, macOS, and Windows.

## Features

**Layout & Design**
- Edit board rows, columns, and grid spacing from the Select-mode inspector
- Drag and drop components on a grid
- Rotate component footprints with pins, or visually angle component bodies
- Flip components to the other side, or swap all front/back sided items at once
- Place vias, traces, and keepout zones
- Auto-numbering for components (R1, R2, U1, etc.)
- Quick footprint library with common passives, DIP ICs, headers, terminals, and Arduino/ESP32 modules
- See a live preview before you place something
- Copy, paste, and duplicate selected items with standard shortcuts

**Routing & Connections**
- Auto-route traces between component pins
- The router tries to avoid collisions and find reasonable paths
- Manage component jumpers and connections visually
- Get suggestions for routing all unconnected pins at once
- Define keepout zones where traces can't go

**Checks & Output**
- Validate connectivity and catch common issues
- Warning mode to highlight problem areas
- Generate a bill of materials from your design


## License

Use it however you want.