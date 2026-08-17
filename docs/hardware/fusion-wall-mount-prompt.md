# Prompt: model the Doorboard door mount in Fusion (via MCP)

Paste everything below the line into Claude with the Fusion MCP connected.

The mount straps **around the door leaf** with heavy-duty ratchet straps — no drilling, no
adhesives. That makes the door itself the structure, which is why the brief spends more
words on swing clearance and slam shock than on wall anchors.

The model looks dimensions up itself and records them as parameters with a confidence and a
source, so a wrong number is a parameter edit rather than a remodel. It only asks about the
handful of things that genuinely are not published.

---

You have a Fusion 360 MCP connected. Design and assemble a mount for a Raspberry-Pi-based
smart doorbell ("Doorboard") that **straps around a door leaf**. Work in **mm**.

## Phase 1 — research the dimensions yourself

Look up the mechanical dimensions for the parts below. Do **not** ask me for anything you can
find published. For each parameter record: **value, source (URL or datasheet name), and
confidence (high / medium / low)**.

Branded parts with published mechanical drawings — find these:

| Part | What to find |
|---|---|
| Raspberry Pi 5 (8 GB) | board outline, 4× M2.5 hole positions, port faces and connector heights |
| Raspberry Pi AI HAT+ **26 TOPS** (Hailo-8) | board outline, GPIO stacking height, **total stack height with the official active cooler** |
| Raspberry Pi Camera Module 3 — Standard **and** Wide NoIR | outline, thickness, hole pattern, lens centre offset, MIPI ribbon width |
| Raspberry Pi 27 W USB-C PSU | brick and plug dimensions |
| Dell **SE2725HM** monitor | panel outline, depth, bezel, **VESA pattern**, **mass**, connector positions and protrusion |
| 2020 aluminium extrusion | 20 × 20 profile, T-slot width, standard M5 T-nut geometry |
| WS2812B LED ring | pick a common size (e.g. 12/16/24 LED) and note its OD/ID |

Where a part has many variants (ESP32-S3 dev boards, USB SSD enclosures, class-D amp boards,
buck converters, USB microphones, small speakers), **choose a specific common product, name
it, and parameterise its dimensions** so I can swap it later. Do not invent a generic size.

**Ask me only for these**, because they are not reliably published or are physical facts you
cannot look up:

1. **Door leaf thickness, width, and height**, and which side the hinges are on.
2. **Door handle/latch position and protrusion** — the mount and straps must not foul it.
3. The 7" touchscreen. Mine reports as **"Mediatrix MPI7010", 1024 × 600, HDMI + USB touch**.
   Try to find it; it is an obscure vendor, so if you cannot find a mechanical drawing say so
   and ask me to measure the outline, thickness, bezel and hole pattern.
4. **Measured** plywood/MDF thickness for laser cutting (nominal 6 mm is often 5.6–5.9 mm)
   and your best guess at my laser kerf unless I give one.
5. 3D printer build volume and material.
6. Target height for the monitor centre and the touchscreen, measured from the floor.

State every assumption you make. I would rather see a list of assumptions than a model that
hides them.

## Phase 2 — parameters, not numbers

Create **named Fusion user parameters** for everything from Phase 1 (`pi_w`, `hat_stack_z`,
`mon_mass`, `mon_vesa`, `panel7_w`, `door_t`, `door_w`, `ply_t`, `kerf`, `print_clear`,
`ext_w = 20`, …). Every sketch references a parameter; no typed literals anywhere.

Include `print_clear` (default 0.3) and `kerf` (default 0.15) and use them in every mating
feature. Keep a parameter table with value + source + confidence, and flag the low-confidence
ones so I know what to check first.

## Phase 3 — the structure, and the load path

The frame is 2020 extrusion, held to the door by **heavy-duty ratchet straps that pass all
the way around the door leaf** (front face → door edge → back face → other edge). Padding at
every contact point; the mount must leave **no marks**.

Before modelling, tell me in words:

- Where the straps run, how many, and what stops them sliding down the door over time.
- **How the load is actually carried.** Friction from strap tension alone tends to creep, and
  a door gets slammed. Consider a top-edge hook or a lip that sits over the door's top edge
  to carry the weight in shear, with the straps providing clamping rather than lift. If you
  think straps alone are sufficient, justify it against the monitor's mass.
- What happens **every time the door closes**: this is a repeated shock load on the whole
  assembly and on the strap tension. Say how the design tolerates it.

Hard geometric constraints from the door:

- Total build depth must let the door **open and close fully** and clear the frame, jamb, and
  any door stop. State the depth you end up with.
- Nothing may foul the handle, latch or hinges.
- The mount adds mass at a distance from the hinge — report the moment about the hinge axis
  so I can sanity-check it against the hinges.
- Cabling crosses to the room only on the hinge side, as a **flexible low-strain loop**:
  exactly one Ethernet and one DC line. Everything else stays local to the frame.

## Phase 4 — the parts

Each as a **separate named Fusion component**.

**2020 extrusion frame** — wall of the assembly. Produce a **cut list** (qty × length), model
each length as its own instance, and model the T-slot profile so printed brackets mate to it.

**Laser-cut wood** (flat, single thickness `ply_t`) — front facing panel with apertures for
the monitor screen area, 7" panel, both cameras, bell button, LED ring, speaker grille,
microphone port and a **privacy-notice plate**; plus interior shelves and load-spreader
panels that distribute strap tension across the door face instead of point-loading it. Nest
all wood parts in one sketch per sheet for DXF export. Wood-to-wood joints use finger joints
or T-slot + captive nut, sized with `kerf`.

**3D-printed parts** — extrusion corner/T brackets; extrusion-to-wood adapters; **strap
capture features** that positively locate each strap so it cannot migrate; padded door-edge
protectors; trays for Pi 5 + AI HAT+ (with the measured stack height plus airflow clearance),
SSD, ESP32-S3, amp, buck converters; LED ring bezel; speaker mount; button housing.

**Two separate camera mounts with different fixed aim angles:**
- *Recognition* (Camera Module 3 Standard) at upper-chest/face height, aimed at a person
  standing **0.8–1.8 m** from the door, as frontal as possible.
- *Visitor* (Wide NoIR) framing the whole scene.
- Give each ±15° of adjustable tilt and **report the resulting aim angle and what it sees at
  1.2 m**. Getting this wrong is the failure mode this build already hit once: a camera
  aimed at the ceiling detects faces and recognises nobody.

Every printed part: state print orientation, avoid supports where possible, use
`print_clear` on all fits.

## Phase 5 — assembly and validation

- Assemble with real **joints**, not floating bodies.
- Run an **interference check**; report and fix every clash.
- Report **total mass**, **centre of gravity**, the CoG's distance from the door face, and the
  moment about the hinge axis.
- Confirm the monitor and touchscreen land at the heights I gave, and that the door still
  closes with the stated depth.

## Non-negotiable constraints

1. **Thermal.** Pi 5 + Hailo + two cameras + a browser is a real heat load. The electronics
   bay needs ventilation with a deliberate airflow path and unobstructed active-cooler intake.
   Describe the path.
2. **Serviceable but closable.** The Pi's USB ports, microSD and SSD must be reachable for
   maintenance yet not casually accessible — this unit is treated as physically stealable.
3. **Two cables in**, both with strain relief.
4. **Visible privacy notice** on the front face. A requirement, not decoration.
5. **No drilling, no adhesives, no marks** — on the door or the walls.
6. MIPI camera ribbons are short and have a bend radius: place the Pi accordingly.

## Deliverables

1. Parameter table with value, source, confidence.
2. The load-path and door-swing explanation from Phase 3.
3. Named components in a jointed assembly.
4. **Extrusion cut list** and **fastener BOM** (M5 T-nuts/bolts, M2.5 standoffs, strap specs
   with a working-load figure).
5. Exports: nested **DXF** per sheet, **STL + STEP** per printed part, **STEP** of the whole
   assembly.
6. Build order, with a **dead-weight load test at full mass, on the door, before any
   electronics are fitted** as an explicit step — including opening and slamming it.
7. Every assumption, listed plainly.

Start with Phase 1: research the dimensions, then ask me only the six questions you cannot
answer yourself.
