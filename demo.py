#!/usr/bin/env python3
"""
Quick self-contained demo of the upgraded engine.
Produces two plots in assets/ (or current directory).
"""

import os
from datetime import datetime
import numpy as np

from geofencing import (
    VisitorManagementSystem, Visitor, Position, ZoneProfile,
    TransitionGraph, BuildingLayout,
    generate_legitimate_path, generate_spoofed_path, generate_extreme_spoofed_path,
)

os.makedirs("assets", exist_ok=True)

# ------------------------------------------------------------------
# Facility: simplified CBK-style layout
# ------------------------------------------------------------------
lobby = ZoneProfile(
    zone_name="main_lobby",
    center=(0.0, 0.0),
    radius=90.0,
    vertices=[(-70, -50), (70, -50), (80, 60), (-60, 65)],
    v_max=2.3, a_max=1.8, zone_type="lobby",
)

parking = ZoneProfile(
    zone_name="parking",
    center=(-150.0, 0.0),
    radius=80.0,
    v_max=8.0, a_max=3.0, zone_type="parking",
)

tg = TransitionGraph()
tg.add_edge("parking", "main_lobby", max_time=120)
tg.add_edge("main_lobby", "parking", max_time=120)

layout = BuildingLayout("Demo Facility", zones=[lobby, parking], transition_graph=tg)

vms = VisitorManagementSystem()
vms.set_building_layout(layout)

# ------------------------------------------------------------------
# Scenario 1 – legitimate
# ------------------------------------------------------------------
visitor1 = Visitor(
    visitor_id="V-LEGIT", name="Alex Okello", badge_tag="B-100",
    entry_time=datetime.now(), allowed_areas=["main_lobby", "parking"],
)
vms.register_visitor(visitor1)

legit_path = generate_legitimate_path(center=(0, 0), n_points=12, gps_accuracy=4.5)
report1 = vms.verify_visitor_location("V-LEGIT", "main_lobby", legit_path)
print("LEGIT  →", report1.risk_level.value, f"score={report1.anomaly_score:.3f}",
      f"σ={report1.position_uncertainty:.1f}m")

# ------------------------------------------------------------------
# Scenario 2 – classic teleport spoof
# ------------------------------------------------------------------
visitor2 = Visitor(
    visitor_id="V-SPOOF", name="Suspicious Guest", badge_tag="B-999",
    entry_time=datetime.now(), allowed_areas=["main_lobby"],
)
vms.register_visitor(visitor2)

spoof_path = generate_spoofed_path(center=(0, 0), n_points=12, gps_accuracy=5.0)
report2 = vms.verify_visitor_location("V-SPOOF", "main_lobby", spoof_path)
print("SPOOF  →", report2.risk_level.value, f"score={report2.anomaly_score:.3f}",
      f"σ={report2.position_uncertainty:.1f}m")

# ------------------------------------------------------------------
# Scenario 3 – extreme multi-jump
# ------------------------------------------------------------------
visitor3 = Visitor(
    visitor_id="V-EXTREME", name="Critical Actor", badge_tag="B-000",
    entry_time=datetime.now(), allowed_areas=["main_lobby"],
)
vms.register_visitor(visitor3)

extreme_path = generate_extreme_spoofed_path(center=(0, 0), n_points=12, gps_accuracy=5.0)
report3 = vms.verify_visitor_location("V-EXTREME", "main_lobby", extreme_path)
print("EXTREME→", report3.risk_level.value, f"score={report3.anomaly_score:.3f}",
      f"σ={report3.position_uncertainty:.1f}m")

# ------------------------------------------------------------------
# Visualise the two interesting cases
# ------------------------------------------------------------------
from geofencing.geofence import GeofenceSystem

gs = vms.geofence_systems["main_lobby"]

fig1 = gs.visualize_path(legit_path, gs.detect_spoofing(legit_path),
                         title="Legitimate Visitor")
fig1.savefig("assets/visitor_legitimate_scenario.png", dpi=120, bbox_inches="tight")
print("Wrote assets/visitor_legitimate_scenario.png")

fig2 = gs.visualize_path(spoof_path, gs.detect_spoofing(spoof_path),
                         title="Spoofed Teleportation")
fig2.savefig("assets/visitor_suspicious_scenario.png", dpi=120, bbox_inches="tight")
print("Wrote assets/visitor_suspicious_scenario.png")

print("\nDemo complete.")
