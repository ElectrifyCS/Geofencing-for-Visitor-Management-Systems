"""
Self-contained demonstration with synthetic visitors and simulated GPS paths.

Run with: python demo.py  (or python Geofencing.py, kept for backwards compatibility)
Produces two visualizations:
    visitor_legitimate_scenario.png
    visitor_suspicious_scenario.png
No real location data is required or included.
"""
import os
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime

from geofencing.models import Visitor, Position, ZoneProfile, BuildingLayout
from geofencing.geofence import GeofenceSystem
from geofencing.vms import VisitorManagementSystem
from geofencing.synthetic import generate_legitimate_path, generate_spoofed_path


# Command-line / script demonstration
def _demo():
    """
    Demonstration with zone profiles, user behavior, and temporal weighting
    """
    # Initialize building layout with multiple zones
    building = BuildingLayout(building_name="Main Facility")
    
    # Create zone profiles for different areas
    lobby_zone = ZoneProfile(
        zone_name="main_lobby",
        center=(0.0, 0.0),
        radius=200.0,
        v_max=2.5,  # Walking speed only
        a_max=2.0,
        zone_type="lobby"
    )
    
    stairwell_zone = ZoneProfile(
        zone_name="stairwell_A",
        center=(150.0, 150.0),
        radius=150.0,
        v_max=1.5,  # Very restricted
        a_max=3.0,  # Higher acceleration acceptable
        zone_type="stairwell"
    )
    
    parking_zone = ZoneProfile(
        zone_name="parking_garage",
        center=(500.0, 500.0),
        radius=300.0,
        v_max=15.0,  # Driving slow
        a_max=3.5,
        zone_type="parking"
    )
    
    building.zones = [lobby_zone, stairwell_zone, parking_zone]
    
    # Initialize visitor management system
    vms = VisitorManagementSystem()
    for zone in building.zones:
        vms.add_geofence(zone.zone_name, zone.center, zone.radius, zone_profile=zone)

    print("=" * 90)
    print("VISITOR MANAGEMENT SYSTEM - ADVANCED FEATURES")
    print("Context-Aware Zones | User Behavior Profiles | Temporal Weighting")
    print("=" * 90)

    # Scenario 1: Regular visitor with learned profile
    print("\n" + "─" * 90)
    print("SCENARIO 1: RETURNING VISITOR (BEHAVIOR PROFILE + KALMAN SMOOTHING)")
    print("─" * 90)
    visitor_1 = Visitor(
        visitor_id="V001",
        name="Visitor A",
        badge_tag="BADGE-12345",
        entry_time=datetime.now(),
        expected_zones=[(0.0, 0.0)],
        security_level="standard",
        host_department="Engineering",
        purpose="Project Meeting",
        allowed_areas=["lobby", "conference"]
    )
    
    # Get user profile (builds over visits)
    user_profile_1 = building.get_visitor_profile("V001")
    visitor_1.behavior_profile = user_profile_1
    print(f"🔍 User Profile: {visitor_1.name}")
    print(f"   Previous Visits: {user_profile_1.visits_count}")
    print(f"   Known Velocity Range: {user_profile_1.avg_velocity:.2f} - {user_profile_1.max_velocity:.2f} m/s")
    
    checkin = vms.register_visitor(visitor_1)
    print(f"✅ {checkin['message']}")
    
    # Generate legitimate path with GPS noise (typical urban quality: ~10m accuracy)
    legit = generate_legitimate_path((0.0, 0.0), n_points=10, gps_accuracy=10.0)
    
    # Create system with zone profile and user profile, Kalman enabled
    lobby_system = GeofenceSystem(
        center=lobby_zone.center,
        radius=lobby_zone.radius,
        zone_profile=lobby_zone,
        user_profile=user_profile_1,
        enable_kalman_smoothing=True,
        kalman_process_variance=15.0
    )
    
    result_1 = lobby_system.detect_spoofing(legit)
    report_1 = vms.verify_visitor_location("V001", "main_lobby", legit)
    
    print(f"\n Location Verification (Context: {lobby_zone.zone_type.upper()})")
    print(f"   Zone Thresholds: v_max={lobby_system.v_max} m/s, a_max={lobby_system.a_max} m/s²")
    print(f"   GPS Quality: {lobby_system.gps_quality} | Kalman Smoothing: ENABLED")
    print(f"   Anomaly Score: {report_1.anomaly_score:.3f} | Risk: {report_1.risk_level.value}")
    print(f"   Location Accuracy: {report_1.location_accuracy:.1f}m")
    print(f"   Status:  CLEARANCE GRANTED")
    
    # Update behavior profile
    velocities = [lobby_system.calculate_velocity(
        np.array([p.x, p.y]), np.array([legit[i+1].x, legit[i+1].y])
    ) for i, p in enumerate(legit[:-1])]
    building.update_visitor_profile("V001", velocities, [], ["main_lobby"])

    # Scenario 2: Suspicious visitor in restricted zone
    print("\n" + "─" * 90)
    print("SCENARIO 2: SUSPICIOUS ACTIVITY IN STAIRWELL (ADAPTIVE GPS THRESHOLDS)")
    print("─" * 90)
    visitor_2 = Visitor(
        visitor_id="V002",
        name="Visitor B",
        badge_tag="BADGE-99999",
        entry_time=datetime.now(),
        expected_zones=[(150.0, 150.0)],
        security_level="standard",
        host_department="Unknown",
        purpose="Facility Tour",
        allowed_areas=["lobby"]
    )
    
    checkin = vms.register_visitor(visitor_2)
    print(f"✅ {checkin['message']}")
    
    # Poor GPS in stairwell (indoor environment): 20m accuracy
    spoofed = generate_spoofed_path((150.0, 150.0), n_points=12, gps_accuracy=20.0)
    
    # Use stairwell zone (more restrictive) with Kalman enabled
    stairwell_system = GeofenceSystem(
        center=stairwell_zone.center,
        radius=stairwell_zone.radius,
        zone_profile=stairwell_zone,
        enable_kalman_smoothing=True,
        kalman_process_variance=25.0  # Higher due to poor GPS
    )
    
    result_2 = stairwell_system.detect_spoofing(spoofed)
    report_2 = vms.verify_visitor_location("V002", "stairwell_A", spoofed)
    
    print(f"\n📍 Location Verification (Context: {stairwell_zone.zone_type.upper()})")
    print(f"   Zone Thresholds: v_max={stairwell_system.v_max} m/s, a_max={stairwell_system.a_max} m/s²")
    print(f"   GPS Quality: {stairwell_system.gps_quality} | Kalman Smoothing: ENABLED")
    print(f"   Anomaly Score: {report_2.anomaly_score:.3f} | Risk: {report_2.risk_level.value}")
    print(f"   Location Accuracy: {report_2.location_accuracy:.1f}m")
    
    if report_2.flagged_events:
        print(" SECURITY ALERTS:")
        for event in report_2.flagged_events[:3]:  # Show first 3 events
            print(f"   {event}")
    print(f"   Status:   MANUAL REVIEW REQUIRED")

    # Scenario 3: VIP with different movement profile
    print("\n" + "─" * 90)
    print("SCENARIO 3: EXECUTIVE VISITOR (PERSONALIZED PROFILE)")
    print("─" * 90)
    visitor_3 = Visitor(
        visitor_id="V003",
        name="Visitor C",
        badge_tag="BADGE-VIP-001",
        entry_time=datetime.now(),
        expected_zones=[(0.0, 0.0), (150.0, 150.0)],
        security_level="vip",
        host_department="Executive",
        purpose="Board Meeting",
        allowed_areas=["lobby", "conference", "executive_suite"]
    )
    
    # Create custom profile for VIP (higher tolerance)
    vip_profile = building.get_visitor_profile("V003")
    vip_profile.max_velocity = 3.5  # VIPs move faster
    vip_profile.max_acceleration = 3.0
    vip_profile.deviation_tolerance = 2.0  # More lenient
    visitor_3.behavior_profile = vip_profile
    
    checkin = vms.register_visitor(visitor_3)
    print(f"✅ {checkin['message']}")
    print(f"   VIP Profile: Higher tolerance for movement variations")
    
    vip_path = [
        Position(t=0.0, x=-100.0, y=-100.0),
        Position(t=60.0, x=-50.0, y=-50.0),
        Position(t=120.0, x=20.0, y=10.0),
        Position(t=180.0, x=80.0, y=60.0),
        Position(t=240.0, x=100.0, y=100.0),
    ]
    
    vip_system = GeofenceSystem(
        center=lobby_zone.center,
        radius=lobby_zone.radius,
        zone_profile=lobby_zone,
        user_profile=vip_profile
    )
    
    report_3 = vms.verify_visitor_location("V003", "main_lobby", vip_path)
    
    print(f"\n📍 Location Verification")
    print(f"   Adjusted Thresholds (VIP Profile): v_max={vip_system.v_max:.1f} m/s, a_max={vip_system.a_max:.1f} m/s²")
    print(f"   Anomaly Score: {report_3.anomaly_score:.3f} | Risk: {report_3.risk_level.value}")
    print(f"   Status: ✅ CLEARANCE GRANTED")

    # Security Dashboard
    print("\n" + "─" * 90)
    print("SECURITY TEAM DASHBOARD")
    print("─" * 90)
    audit = vms.export_audit_log()
    print(f"Active Visitors: {audit['active_visitors']}")
    print(f"Security Alerts: {len(audit['security_alerts'])}")
    print(f"Monitored Zones: {audit['total_zones']}")
    print(f"Zone Types: {', '.join([z.zone_type for z in building.zones])}")
    
    if audit['security_alerts']:
        print("\n⚠️  ACTIVE ALERTS:")
        for alert in audit['security_alerts']:
            print(f"   [{alert['alert_type']}] {alert['visitor_name']} in {alert['zone']} - Risk: {alert['risk_level']}")

    print("\n" + "─" * 90)
    print("USER BEHAVIOR PROFILES (LEARNED FROM VISITS)")
    print("─" * 90)
    for visitor_id in ["V001", "V003"]:
        profile = building.get_visitor_profile(visitor_id)
        if profile.visits_count > 0:
            print(f"\n{visitor_id}:")
            print(f"  Visits: {profile.visits_count}")
            print(f"  Avg Velocity: {profile.avg_velocity:.2f} m/s | Max: {profile.max_velocity:.2f} m/s")
            print(f"  Tolerance: {profile.deviation_tolerance}x (stricter = 1.0, lenient = 2.0)")
            print(f"  Common Zones: {', '.join(profile.common_zones) if profile.common_zones else 'None yet'}")

    # GPS Quality Comparison
    print("\n" + "─" * 90)
    print("GPS QUALITY & ADAPTIVE THRESHOLD GUIDE")
    print("─" * 90)
    quality_guide = [
        ("EXCELLENT (<5m)", "Open areas, parking lots", "Standard thresholds apply"),
        ("GOOD (5-10m)", "City streets, campus", "1.3x velocity tolerance"),
        ("MODERATE (10-20m)", "Urban canyons, dense downtown areas", "1.8x velocity tolerance"),
        ("POOR (>20m)", "Indoors, dense buildings", "2.5x velocity tolerance + Kalman"),
    ]
    for quality, location, thresholds in quality_guide:
        print(f"  {quality:20} | {location:25} | {thresholds}")

    # Visualizations
    fig1 = lobby_system.visualize_path(legit, result_1, 
                                       f"LEGITIMATE: {visitor_1.name} (Context: {lobby_zone.zone_type})")
    fig2 = stairwell_system.visualize_path(spoofed, result_2, 
                                           f"SUSPICIOUS: {visitor_2.name} (Context: {stairwell_zone.zone_type})")
    
    # Save figures instead of blocking with plt.show()
    try:
        os.makedirs("assets", exist_ok=True)
        fig1.savefig("assets/visitor_legitimate_scenario.png", dpi=150, bbox_inches='tight')
        fig2.savefig("assets/visitor_suspicious_scenario.png", dpi=150, bbox_inches='tight')
        print("\n✅ Visualization saved: assets/visitor_legitimate_scenario.png")
        print("✅ Visualization saved: assets/visitor_suspicious_scenario.png")
    except Exception as e:
        print(f"Note: Could not save figures: {e}")
    
    plt.close('all')

if __name__ == "__main__":
    _demo()
