# Plan: Identify and Set Properties of Unknown Custom Devices

## Problem

When a custom device is loaded in Micro-Manager (e.g. an Arduino illumination
controller, a proprietary filter wheel, a third-party stage adapter), the agent
has no prior knowledge of its property names or valid values.  The existing
tools `get_device_property` and `set_device_property` both require knowing the
property name in advance.  `list_devices` only returns device names; nothing
bridges the gap to property names.

The goal is to let Claude discover a device's properties at run-time, inspect
their metadata (type, allowed values, read-only flag, numeric limits), and then
call `set_device_property` with confidence.

---

## Micro-Manager Core API Used

All methods are available on pycro-manager's `Core` object
(`ctrl.core`).  No new dependencies are needed.

| Core method | Returns | Purpose |
|---|---|---|
| `core.get_device_property_names(device)` | `mmcorej_StrVector` | All property names for a device |
| `core.get_property_type(device, prop)` | MM `PropertyType` | `"String"`, `"Float"`, or `"Integer"` |
| `core.is_property_read_only(device, prop)` | bool | Whether the property can be set at run-time |
| `core.is_property_pre_init(device, prop)` | bool | Whether setting it requires a full device re-init |
| `core.get_allowed_property_values(device, prop)` | `mmcorej_StrVector` | Non-empty only for enum/discrete-valued properties |
| `core.has_property_limits(device, prop)` | bool | Whether numeric limits exist |
| `core.get_property_lower_limit(device, prop)` | float | Lower bound (when `has_property_limits` is True) |
| `core.get_property_upper_limit(device, prop)` | float | Upper bound |

`get_property_type` returns a Java `PropertyType` enum.  Call `str()` on it
and strip the class prefix to get `"String"`, `"Float"`, or `"Integer"`.
Verify the exact string representation during implementation against the
pycro-manager/mmcorej version in use — add a small helper if stripping is
needed.

---

## New Tools

### 1. `list_device_properties(device)`

**Location:** `microclaw/tools.py`

```python
def list_device_properties(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
) -> dict:
    props = _str_vector(ctrl.core.get_device_property_names(device))
    return {"device": device, "properties": props, "count": len(props)}
```

**Purpose:** Returns every property name exposed by `device`.  Claude calls
this first when the user mentions a device whose properties are not known.

---

### 2. `get_device_property_info(device, property)`

**Location:** `microclaw/tools.py`

```python
def get_device_property_info(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
    property: str,
) -> dict:
    read_only = bool(ctrl.core.is_property_read_only(device, property))
    pre_init  = bool(ctrl.core.is_property_pre_init(device, property))
    prop_type = str(ctrl.core.get_property_type(device, property)).split(".")[-1]

    allowed_sv = ctrl.core.get_allowed_property_values(device, property)
    allowed = _str_vector(allowed_sv) if allowed_sv.size() > 0 else None

    has_limits = bool(ctrl.core.has_property_limits(device, property))
    lower = ctrl.core.get_property_lower_limit(device, property) if has_limits else None
    upper = ctrl.core.get_property_upper_limit(device, property) if has_limits else None

    current = ctrl.core.get_property(device, property)

    return {
        "device": device,
        "property": property,
        "current_value": current,
        "type": prop_type,
        "read_only": read_only,
        "pre_init": pre_init,
        "allowed_values": allowed,   # list[str] or null
        "lower_limit": lower,        # float or null
        "upper_limit": upper,        # float or null
    }
```

**Purpose:** Gives Claude everything it needs to decide whether a property is
settable and what values are valid before calling `set_device_property`.

---

### 3. `get_full_device_state(device)`

**Location:** `microclaw/tools.py`

```python
def get_full_device_state(
    ctrl: MicroscopeController,
    guard: SafetyGuard,
    device: str,
) -> dict:
    props = _str_vector(ctrl.core.get_device_property_names(device))
    state = {}
    for p in props:
        try:
            state[p] = ctrl.core.get_property(device, p)
        except Exception as exc:
            state[p] = f"<error: {exc}>"
    return {"device": device, "state": state}
```

**Purpose:** One-shot snapshot of every property's current value.  Useful for
orientation — e.g. "show me everything about the Arduino device."

---

## Typical Discovery Workflow

```
User: "Set the Arduino illumination to pattern 3."

1. Claude → list_devices()
   ← {"devices": ["Arduino-Switch", "Camera", ...]}

2. Claude → list_device_properties("Arduino-Switch")
   ← {"properties": ["State", "Label", "Sequence"], "count": 3}

3. Claude → get_device_property_info("Arduino-Switch", "State")
   ← {"type": "Integer", "read_only": false, "pre_init": false,
      "allowed_values": null, "lower_limit": 0, "upper_limit": 7,
      "current_value": "0"}

4. Claude → set_device_property("Arduino-Switch", "State", "3")
   ← {"status": "Set Arduino-Switch.State = '3'."}
```

---

## Changes Required

### `microclaw/tools.py`

- Add the three functions above.
- Add all three to `TOOL_REGISTRY`:
  ```python
  "list_device_properties": list_device_properties,
  "get_device_property_info": get_device_property_info,
  "get_full_device_state": get_full_device_state,
  ```

### `microclaw/tools_schema.py`

Add three entries to `TOOLS` (before the final tool so `TOOLS_CACHED` keeps
the `cache_control` on the last entry):

```python
{
    "name": "list_device_properties",
    "description": (
        "List all property names exposed by a loaded Micro-Manager device. "
        "Call this when you encounter a device whose properties are not known. "
        "Follow up with get_device_property_info to learn each property's type "
        "and valid values before calling set_device_property."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device": {"type": "string", "description": "Device name from list_devices."},
        },
        "required": ["device"],
    },
},
{
    "name": "get_device_property_info",
    "description": (
        "Return metadata for a single Micro-Manager device property: "
        "its current value, data type (String/Float/Integer), whether it is "
        "read-only or pre-init only, the list of allowed values (for enum "
        "properties), and numeric limits. Always call this before "
        "set_device_property on a property you have not used before."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device":   {"type": "string", "description": "Device name."},
            "property": {"type": "string", "description": "Property name."},
        },
        "required": ["device", "property"],
    },
},
{
    "name": "get_full_device_state",
    "description": (
        "Return the current value of every property of a Micro-Manager device "
        "in a single call. Use this to orient yourself about an unknown device "
        "or to report its complete state to the user."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "device": {"type": "string", "description": "Device name."},
        },
        "required": ["device"],
    },
},
```

### `microclaw/agent.py` — SYSTEM_PROMPT addition

Add a paragraph to the existing guidelines:

```
Device property discovery:
- When the user references a device whose properties you do not know, call
  list_device_properties(device) to enumerate them, then
  get_device_property_info(device, property) on the specific property to learn
  its type, allowed values, and numeric limits before calling set_device_property.
- Do not attempt to set a property whose get_device_property_info result shows
  read_only=true or pre_init=true — explain the limitation to the user instead.
- Use get_full_device_state(device) when the user asks for a complete overview
  of a device's current settings.
```

---

## Safety Considerations

The existing `SafetyGuard.check_property` is a blocklist: any property not
listed in `forbidden_properties` is implicitly allowed.  Newly discovered
properties are therefore usable immediately without any safety config change,
which is the correct default for custom devices.

Two additional guard rails worth enforcing:

1. **Read-only enforcement in `set_device_property`:** Rather than letting the
   call reach MM and get a Java exception (which is caught generically), query
   `core.is_property_read_only` inside `set_device_property` before calling
   `core.set_property` and return a clear error dict if true.  This gives the
   user a better message.  Make this optional via a flag on the function so the
   existing tests don't need to mock the new Core call.

2. **Pre-init property warning:** Similarly, query `core.is_property_pre_init`
   and warn (but do not block) — the user might intentionally want to change a
   pre-init property and then reload the device manually.

---

## Testing

### Unit tests (`tests/test_tools.py`)

All three new tools follow the same mock pattern as the existing device
property tests.

```python
class TestListDeviceProperties:
    def test_returns_property_names(self, mock_ctrl, unconstrained_guard):
        sv = MagicMock()
        sv.size.return_value = 2
        sv.get.side_effect = ["State", "Label"]
        mock_ctrl.core.get_device_property_names.return_value = sv
        result = list_device_properties(mock_ctrl, unconstrained_guard, device="Arduino-Switch")
        assert result["device"] == "Arduino-Switch"
        assert result["properties"] == ["State", "Label"]
        assert result["count"] == 2

    def test_empty_device(self, mock_ctrl, unconstrained_guard):
        sv = MagicMock()
        sv.size.return_value = 0
        mock_ctrl.core.get_device_property_names.return_value = sv
        result = list_device_properties(mock_ctrl, unconstrained_guard, device="Dummy")
        assert result["properties"] == []


class TestGetDevicePropertyInfo:
    def test_integer_with_limits(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.is_property_read_only.return_value = False
        mock_ctrl.core.is_property_pre_init.return_value = False
        mock_ctrl.core.get_property_type.return_value = "Integer"
        sv_empty = MagicMock(); sv_empty.size.return_value = 0
        mock_ctrl.core.get_allowed_property_values.return_value = sv_empty
        mock_ctrl.core.has_property_limits.return_value = True
        mock_ctrl.core.get_property_lower_limit.return_value = 0.0
        mock_ctrl.core.get_property_upper_limit.return_value = 7.0
        mock_ctrl.core.get_property.return_value = "0"
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Arduino-Switch", property="State"
        )
        assert result["read_only"] is False
        assert result["lower_limit"] == 0.0
        assert result["upper_limit"] == 7.0
        assert result["allowed_values"] is None

    def test_enum_property(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.is_property_read_only.return_value = False
        mock_ctrl.core.is_property_pre_init.return_value = False
        mock_ctrl.core.get_property_type.return_value = "String"
        sv = MagicMock(); sv.size.return_value = 2; sv.get.side_effect = ["On", "Off"]
        mock_ctrl.core.get_allowed_property_values.return_value = sv
        mock_ctrl.core.has_property_limits.return_value = False
        mock_ctrl.core.get_property.return_value = "On"
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Shutter", property="State"
        )
        assert result["allowed_values"] == ["On", "Off"]

    def test_read_only_property(self, mock_ctrl, unconstrained_guard):
        mock_ctrl.core.is_property_read_only.return_value = True
        mock_ctrl.core.is_property_pre_init.return_value = False
        mock_ctrl.core.get_property_type.return_value = "String"
        sv_empty = MagicMock(); sv_empty.size.return_value = 0
        mock_ctrl.core.get_allowed_property_values.return_value = sv_empty
        mock_ctrl.core.has_property_limits.return_value = False
        mock_ctrl.core.get_property.return_value = "DemoCam"
        result = get_device_property_info(
            mock_ctrl, unconstrained_guard, device="Core", property="Camera"
        )
        assert result["read_only"] is True


class TestGetFullDeviceState:
    def test_returns_all_properties(self, mock_ctrl, unconstrained_guard):
        sv = MagicMock(); sv.size.return_value = 2; sv.get.side_effect = ["Gain", "Binning"]
        mock_ctrl.core.get_device_property_names.return_value = sv
        mock_ctrl.core.get_property.side_effect = ["1", "1"]
        result = get_full_device_state(mock_ctrl, unconstrained_guard, device="Camera")
        assert result["state"] == {"Gain": "1", "Binning": "1"}

    def test_error_on_one_property_does_not_abort(self, mock_ctrl, unconstrained_guard):
        sv = MagicMock(); sv.size.return_value = 2; sv.get.side_effect = ["Good", "Bad"]
        mock_ctrl.core.get_device_property_names.return_value = sv
        mock_ctrl.core.get_property.side_effect = ["ok", RuntimeError("read error")]
        result = get_full_device_state(mock_ctrl, unconstrained_guard, device="Camera")
        assert result["state"]["Good"] == "ok"
        assert "error" in result["state"]["Bad"]
```

### Integration test (`tests/test_integration.py`)

Against the Demo config (which loads a `Camera` device with known properties):

```python
def test_list_camera_properties(demo_ctrl, unconstrained_guard):
    result = list_device_properties(demo_ctrl, unconstrained_guard, device="Camera")
    assert "Binning" in result["properties"]
    assert result["count"] > 0

def test_get_camera_binning_info(demo_ctrl, unconstrained_guard):
    result = get_device_property_info(demo_ctrl, unconstrained_guard,
                                      device="Camera", property="Binning")
    assert result["read_only"] is False
    assert result["allowed_values"] is not None  # Binning is an enum in Demo

def test_get_full_camera_state(demo_ctrl, unconstrained_guard):
    result = get_full_device_state(demo_ctrl, unconstrained_guard, device="Camera")
    assert "Binning" in result["state"]
```

---

## Rollout Order

1. Implement `list_device_properties`, `get_device_property_info`,
   `get_full_device_state` in `tools.py`.
2. Add unit tests and confirm they pass with mocks.
3. Add schema entries in `tools_schema.py`.
4. Update `SYSTEM_PROMPT` in `agent.py`.
5. Run integration tests against Demo config to verify Core API call signatures.
6. Optionally add read-only enforcement to `set_device_property` as described
   in the Safety Considerations section.
