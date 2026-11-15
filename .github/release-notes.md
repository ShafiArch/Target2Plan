Target2Plan for Windows, macOS and Linux: the same app as https://shafiarch.github.io/Target2Plan/, running on your computer without a browser.

New in 1.1.0:
* Plan editor: **Edit selected** in Schematic Design (or **Edit plan** in a plan's detail window) opens plans on a canvas. Move and resize rooms with snapping, and walls, doors, windows, furniture and V / A follow every change; Ctrl+D moves the door between two selected rooms to its next place; Fill off makes a line drawing and Text hides the room labels; a folding panel shows the selected room, short checks, the rooms and the Pareto front with the plan moving on it. Replace, Previous and Next, Save (with the session), Discard, and Export as JPG, PNG with a transparent background, DXF for CAD (AutoCAD saves it as DWG) or a PDF to scale.
* Faster editing: the plan editor and the drawing canvas redraw with a fraction of the work they took (a few dozen drawing calls a frame instead of over a thousand), so dragging stays smooth on large and high resolution screens, and the mouse wheel zooms smoothly instead of in jumps.
* Drawing canvas: select with Ctrl or Shift + click or a box, Ctrl + drag to copy, copy and paste, duplicate, undo and redo, nudge with the arrow keys, turn a room with R, snap to the edges and centres of the other rooms, and show or hide the grid, the room colours and the room text.
* Balconies stay on their room when rooms were deleted, pasted or added again while drawing (before, they could float away from the plan).
* V instead of Q for the view index on the cards and sheets.
* The Windows installer shows the Target2Plan logo.
* Version 1.0 stays online at https://shafiarch.github.io/Target2Plan/v1/.

Download the file for your computer from the assets below:
* Windows: the `.exe` installer (or the `.msi`)
* macOS, Apple silicon and Intel: the `.dmg`
* Linux: the `.AppImage` (or the `.deb` for Debian and Ubuntu, the `.rpm` for Fedora)

These builds are not code-signed yet, so the system asks once before the first start:
* Windows: if SmartScreen says "Windows protected your PC", click More info, then Run anyway.
* macOS: open the app; when macOS says it cannot verify it, go to System Settings, Privacy & Security, click Open Anyway and enter your password.

© 2026 Shafiul Islam. Licensed under CC BY-NC 4.0.
