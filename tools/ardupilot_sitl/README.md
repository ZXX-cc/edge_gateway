# HD SITL mission

Add this location to `ardupilot/Tools/autotest/locations.txt`:

```text
Changchun_HD=44.0474824,125.3614503,190,0
```

Mission files:

- `HD.original.waypoints` keeps the source mission unchanged. Item 1 uses MAVLink command `84` (`MAV_CMD_NAV_VTOL_TAKEOFF`), suitable for VTOL/QuadPlane testing.
- `HD.copter-sitl.waypoints` changes item 1 to MAVLink command `22` (`MAV_CMD_NAV_TAKEOFF`), which is the safer direct test file for ArduCopter SITL.

The mission uses `GLOBAL_RELATIVE_ALT` (`frame=3`) for task items, with a 96 m takeoff leg and 126 m route altitude. Item 4 is `LOITER_TURNS`, and item 6 is `DO_JUMP` back to item 2 five times.
