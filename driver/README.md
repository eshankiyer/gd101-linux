Build and usage instructions are in the [repository README](../README.md).

`build.py` creates the native Linux embedding library. `build_wine_bridge.py`
creates a 32-bit Windows DLL and test probes. The DLL forwards calls to
`tools/gd101_bridge.py`; it does not load the vendor DLL.

`probe_wine_write.exe` is intended only for the fake broker. It exercises
message writes and active programming-pin requests in simulation. Do not use
that probe with a real adapter.

`probe_wine_bridge.exe` exercises real adapter/channel lifecycle operations
when connected to a hardware broker. It is not equivalent to the narrower
identity/voltage-only `tools/check_gd101_adapter.py` check.
