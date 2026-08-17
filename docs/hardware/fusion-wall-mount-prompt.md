# Prompt: model the Doorboard wall mount in Fusion (via MCP)

Paste everything below the line into Claude with the Fusion MCP connected. It is written to
make the model **ask for the measurements it cannot know before it draws anything** — that
one instruction is the difference between a usable model and a confident fantasy.

Keep this file updated as the real mount evolves; it is the design brief, not a transcript.

---

You have a Fusion 360 MCP connected. I want you to design and assemble a **wall-mounted
frame** for a Raspberry-Pi-based smart doorbell ("Doorboard"). Work in **mm**.

## Phase 0 — measure before you model (do this first, do not skip)

Do **not** start sketching. First, produce a numbered **measurement checklist** of every
dimension you need from me that you cannot know reliably, then stop and wait for my answers.

I already know these and you should treat them as given:

| Item | Known | Confidence |
|---|---|---|
| Raspberry Pi 5 board | 85 × 56 mm, 4× M2.5 holes on a 58 × 49 mm rectangle, 3.5 mm in from edges | high |
| 2020 aluminium extrusion | 20 × 20 mm, 6 mm T-slot, M5 T-nuts | high |
| Wallboard display | Dell SE2725HM, ~24" 1080p, VESA | model known, **dims/VESA/weight must be measured** |
| DoorPad touchscreen | "Mediatrix MPI7010", 7", 1024 × 600, HDMI + USB touch | **outline, bezel, hole pattern must be measured** |
| Cameras | 2× Raspberry Pi Camera Module 3 (one Standard, one Wide NoIR), ~25 × 24 × ~11.5 mm | medium — verify hole pattern |
| AI accelerator | Raspberry Pi AI HAT+ 26 TOPS (Hailo-8), HAT form factor on the 40-pin header | **stack height with active cooler must be measured** |

Things you will almost certainly need to ask me for — measure-or-ask, never assume:

- Exact outline, thickness, bezel width and mounting-hole pattern of the 7" panel and its
  driver board (these vary wildly between vendors).
- Monitor: outline, thickness, VESA pattern (100 × 100 vs 75 × 75), **mass**, and where the
  cable/power connectors sit and how far they protrude.
- AI HAT+ stack height above the Pi PCB with the active cooler fitted, and the resulting
  total footprint.
- USB SSD enclosure dimensions; ESP32-S3 dev board outline and hole pattern; WS2812B ring
  outer/inner diameter; speaker diameter and depth; class-D amp board size; USB microphone
  size; SCD40/41 breakout size; power-supply brick size.
- Wall: what it is (plaster, concrete, tile), and **what the ratchet straps actually anchor
  to** — this is load-bearing to the whole design, so ask explicitly and do not guess.
- Sheet stock for laser cutting: material and **exact measured thickness** (nominal 6 mm
  plywood is often 5.6–5.9 mm), plus your laser's kerf.
- 3D printer build volume, nozzle/layer height, and preferred material.
- Target standing-eye height for the wallboard and the reach height for the touchscreen.

## Phase 1 — parameters, not numbers

Once I have answered, create **named Fusion user parameters** for every dimension above
(e.g. `pi_w`, `pi_h`, `hat_stack_z`, `panel7_w`, `mon_vesa`, `ply_t`, `kerf`, `print_clear`,
`ext_w = 20`). Every sketch must reference parameters, never typed literals.

This is the point of the exercise: my measurements will be wrong somewhere, and I want to
fix a number and rebuild rather than remodel. Include `print_clear` (3D-print clearance,
default 0.3) and `kerf` (default 0.15) and use them in every mating feature.

## Phase 2 — the parts

Create each as a **separate Fusion component** with a clear name, so the assembly is real
and not one lump body.

**Structure — 2020 extrusion frame**
- A wall-parallel rectangular frame sized to carry the monitor, with a vertical spine.
- Produce a **cut list** (quantity × length) as a table; model each length as its own
  component instance.
- Model the T-slot profile properly, so printed brackets can be designed against it.

**Wall attachment — heavy-duty ratchet straps**
- Design **strap capture features** (printed or laser-cut) that positively locate the straps
  so they cannot migrate under vibration or creep.
- Straps carry tension only; design the load path so the frame is held **flat against the
  wall** and cannot rotate about a horizontal axis. Tell me the assumed load path in words
  and where you expect the highest stress.
- Every wall/strap contact point needs a padded interface (EVA/felt pocket) — the mount must
  leave **no marks**, which rules out adhesives.

**Laser-cut wood (flat sheet, thickness = `ply_t`)**
- Front facing panel with apertures for: monitor screen area, 7" panel, both cameras, bell
  button, LED ring, speaker grille, microphone port, and a privacy-notice plate.
- Rear/interior load-spreader panels and any internal shelves.
- All wood parts must be **flat, single-thickness, and nested in one sketch per sheet** for
  DXF export. Use finger joints or T-slot-and-captive-nut joints where wood meets wood, and
  size the slots with `kerf`.

**3D-printed parts**
- Extrusion corner/T brackets, and adapters from extrusion to the wood panels.
- Cradles/trays: Pi 5 + AI HAT+ (with the measured stack height and airflow clearance),
  SSD holder, ESP32-S3 holder, amp, buck converters.
- Camera mounts: **two separate mounts with different fixed aim angles.** The recognition
  camera sits at upper-chest/face height aimed at a person standing 0.8–1.8 m away, as
  frontal as possible; the wide visitor camera frames the whole scene. Give each an
  adjustable tilt of at least ±15° and tell me the resulting aim angle.
- LED ring bezel, speaker mount, button housing.
- Every printed part: no-support printing where possible, state the intended print
  orientation, and use `print_clear` on all fits.

## Phase 3 — assembly

- Assemble with real **joints** (rigid/revolute as appropriate), not free-floating bodies.
- Run an **interference check** and report every clash you find and fix.
- Report total **mass and centre of gravity**, and how far the CoG sits from the wall plane
  — this is what the straps have to resist.
- Verify the monitor and touchscreen are at the heights I gave you, and that connectors and
  the cable loop have clearance.

## Hard constraints (violating these is a failed design, tell me instead of working around)

1. **Thermal.** The Pi 5 + Hailo + two cameras + a browser is a real heat load. The
   electronics bay needs ventilation with a deliberate airflow path, and the AI HAT+ active
   cooler needs unobstructed intake. Tell me the path.
2. **Serviceability.** The Pi's USB ports, microSD slot and SSD must be reachable for
   maintenance, but the enclosure must be **lockable/closable** so they are not casually
   accessible — the project's trust model treats the door unit as physically stealable.
3. **Cabling.** Exactly two cables enter the frame: one Ethernet, one DC power. Everything
   else stays internal. Provide strain relief for both.
4. **A visible privacy notice** must be mounted on the front face. It is a requirement, not
   decoration.
5. **No drilling, no adhesives, no marks** on the wall.
6. Both cameras use MIPI ribbon cables — respect their limited length and bend radius, and
   place the Pi accordingly.

## Deliverables

1. The measurement checklist (Phase 0), before anything else.
2. A parameter table, with defaults and units.
3. Named components and a joined assembly.
4. **Extrusion cut list**, and a **fastener BOM** (M5 T-nuts/bolts, M2.5 standoffs, etc.).
5. Export-ready: **DXF** for every laser-cut sheet (nested), **STL + STEP** for printed
   parts, and a **STEP** of the whole assembly.
6. A short build order, with a **dead-weight load test at full expected mass before any
   electronics are mounted** as an explicit step.
7. Anything you had to assume, listed plainly at the end. I would rather see a list of
   assumptions than a model that hides them.

Start with Phase 0 only.
