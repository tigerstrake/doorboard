# Door assembly — removable "door backpack"

From handoff §3. Indoor, hallway-facing, removable, no drilling.

## Structure

- Two padded over-door hooks carry the load from the top edge.
- Rigid lightweight front frame: 2020/2040 aluminum extrusion + facing panel (aluminum composite, thin plywood, or printed panels).
- Large monitor on VESA plate; 7" touchscreen below it; cameras, bell button, LED ring, speaker, microphone on the front face.
- Lockable, ventilated enclosure behind the front panel: Pi 5 + AI HAT+, SSD, ESP32, power distribution. USB and microSD physically inaccessible without the key (trust model).
- Inside-door load-spreader/tension bar + cam-buckle straps for stabilization; felt/EVA padding at every door contact point.
- Flexible low-strain cable loop at the hinge side: one Ethernet + one protected power/low-voltage DC line from inside the room. All other wiring stays local to the frame.

## Camera placement

- Recognition camera (Standard): upper-chest/face height, aimed at the expected visitor standing zone 0.8–1.8 m from the door, as frontal as possible.
- Visitor camera (Wide NoIR): framed for the whole visitor scene; IR illuminator is the low-light upgrade path.
- Visible camera/video-message notice text mounted on the front face (privacy requirement).

## Build order (safety)

1. Assemble frame, hooks, straps. 2. **Dead-weight structural test** at full expected load before any electronics are mounted. 3. Mount displays, verify door swing/clearance/closure. 4. Mount electronics enclosure and wiring. 5. Thermal soak test with everything running (feeds the M7 acceptance test).

End-of-year removal must leave no marks: this constrains adhesives and padding choices.
