"""door-visiond — real-time vision service (Hailo owner, identity cache).

Public entry points live in submodules to keep package import light (no FastAPI
import just to touch the adapter types). See ``door_visiond.adapters`` for the
``VisionPipeline`` protocol consumed by the simulator, ``door_visiond.pipeline``
for the pipeline core + capture backends, and ``door_visiond.service`` for the
service wiring.
"""
