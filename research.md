# Controlling Micro-Manager from Python: Research Report

**Project:** Natural language AI agent for Java Micro-Manager  
**Date:** 2026-05-14  
**Constraint:** Must use the Java-native Micro-Manager (MMStudio); pymmcore and pymmcore-plus are excluded.  
**Key requirement:** The biologist must be able to watch the MM GUI respond in real time to their natural language commands — seeing images appear in the camera preview window, watching acquisitions execute, and observing hardware state changes. Headless (no GUI) operation is a secondary, optional mode for running verified protocols unattended.

---

## Executive Summary

Six potential approaches were investigated. **Pycro-Manager** is the clear choice for this project. It is the only maintained, practical solution that connects to a running Java MM application (with full GUI visible) and can drive it from Python — so the biologist watches the microscope respond in the same MM interface they already know. The remaining approaches are either archived, require enormous custom Java development, or cannot drive the running MM GUI at all.

---

## Background: Why the Distinction Matters

There are two fundamentally different ways to control Micro-Manager hardware from Python:

**Path A — Bypass Java entirely:** Use pymmcore or pymmcore-plus, which load the C++ `MMCore` library directly into Python via compiled bindings. The Java Micro-Manager application never runs. This gives raw hardware control but loses all MMStudio functionality (acquisition engine, plugin ecosystem, GUI). The user has excluded this path.

**Path B — Control the Java application:** Run the actual Java Micro-Manager (MMStudio) and send commands to it from Python via inter-process communication (IPC). This preserves the full MM environment, all plugins, the hardware configuration system, and the acquisition engine. This is what the user wants.

All approaches reviewed below attempt Path B unless noted otherwise.

---

## Option 1: Pycro-Manager (Recommended)

### What it is

Pycro-Manager (GitHub: `micro-manager/pycro-manager`, PyPI: `pycromanager`) is a Python library purpose-built to control the Java Micro-Manager from Python. It was created at UCSF/Berkeley, published in *Nature Methods* in 2021, and is actively maintained as of 2026.

It is **completely different from pymmcore.** Pymmcore loads C++ MMCore into Python. Pycro-Manager sends messages over a network socket to a running Java MM application and receives results back. The Java process does all the real work; Python is the remote commander.

### How it works technically

**Java side:** When you enable "Run server on port 4827" in Micro-Manager's Tools → Options dialog, MM starts a `ZMQServer` embedded in the MM application. This server listens for incoming requests via ZeroMQ (using JeroMQ, a pure-Java ZMQ implementation).

**Python side:** The `pycromanager` package sends structured JSON messages over ZMQ to that server. Each message specifies a Java class, method name, argument types, and arguments. The Java server executes the method on the real Java object and returns the result. Python receives the result and constructs a "shadow" Python object that mirrors the Java object, with method names automatically translated from `camelCase` to `snake_case`.

**The communication chain is:**  
`Python script` → ZeroMQ socket → `MM ZMQServer (Java)` → `MMCoreJ (Java/JNI)` → `C++ MMCore` → hardware device adapters

**Primary mode — GUI running:** The biologist opens MM normally. The agent connects via ZMQ and sends commands. Every command executes inside the running MM application, so its effects are immediately visible in the MM GUI: images appear in the preview window, stage position indicators update, acquisition progress is shown in MM's viewer. The biologist can also interact with the MM GUI between agent commands — adjusting display contrast, inspecting the device property browser, etc.

**Secondary mode — headless:** The `start_headless()` function launches MM without a GUI for unattended operation. This is appropriate once a protocol has been verified to work correctly in the GUI-visible mode. The same Python code runs in both modes without modification.

### What it can control, and what the biologist sees

**Live camera preview:**  
Calling `studio.live().set_live_mode_on(True)` starts MM's live camera preview exactly as if the biologist had clicked the Live button themselves. The camera preview window opens in the MM GUI, streaming images in real time. `set_live_mode_on(False)` stops it. Snap (`studio.live().snap(True)`) acquires a single frame and displays it in MM's snap/live window — again, identical to pressing Snap in the GUI.

**Hardware property changes:**  
`core.set_property(device, property, value)` — e.g., setting laser power, switching filter wheels, changing camera gain — updates the hardware and is immediately reflected in MM's Device Property Browser. The biologist can open the browser and see the values the agent just set.

**Stage movement:**  
`core.set_xy_position(x, y)` and `core.set_position(z)` move the stage, and MM's stage position display updates accordingly. The biologist can see where the stage is at all times.

**Multi-dimensional acquisitions:**  
Acquisitions (Z-stacks, time-lapses, multi-channel, multi-position) launched via pycro-manager's `Acquisition` class use MM's standard AcqEngJ acquisition engine. MM opens its normal acquisition viewer window, images appear as they are captured, and a progress indicator runs. The biologist watches the acquisition play out in the same viewer they would see if they had set it up manually via the MDA dialog.

**Configuration and presets:**  
`core.set_config(group, preset)` applies a hardware preset exactly as clicking one in the MM config group panel would.

**Adaptive/AI-driven acquisitions:**  
- **Hooks:** Python code injected at specific points in the acquisition loop (before hardware moves, after image capture). The AI agent can inspect each image and decide whether to continue, repeat, or change parameters.
- **Image processors:** Every captured frame arrives in Python as a numpy array, enabling on-the-fly segmentation, classification, or any image analysis. The agent can redirect the acquisition based on what it finds.
- **Event queues:** The agent can generate new acquisition events (move to a new position, change channel, etc.) in response to what it is seeing, enabling fully adaptive protocols.

### Maintenance and community status

- GitHub repository: 2,090+ commits, continuous PR activity as of 2025
- Version 1.0.0 released August 28, 2024; regularly updated
- Published: Pinkard et al., *Nature Methods* 2021 (PMID 33674797)
- Widely used in academic labs for smart/adaptive microscopy workflows
- Active community on the Image.sc forum

### Ease of use for a biologist

**One-time setup** (requires technical assistance): install MM nightly build, check "Run server on port 4827" in Tools → Options, `pip install pycromanager` in the agent environment.

**Day-to-day use**: the biologist opens MM as they normally would, sets up or loads their hardware configuration, then opens the agent chat interface (a separate window). They type their request. The agent sends commands to MM, and the biologist watches the GUI respond — live view starts, the stage moves, images appear, the acquisition viewer opens and fills. They never see Python code. They can interact with MM's GUI at any point between agent commands, just as they would normally.

### Key limitations

1. **Version synchronization:** The `pycromanager` Python package version must match the ZMQ server version embedded in the MM nightly build. Running mismatched versions causes connection errors. Both must be kept updated together.
2. **Bandwidth ceiling:** Image data transfers over ZMQ max out at roughly 100–200 MB/s. Adequate for most cameras; a constraint for very high-speed acquisition requiring Python-side processing without disk intermediary.
3. **Single port limitation:** Blocking calls on a single ZMQ port prevent concurrent requests. Multiple simultaneous operations require separate port numbers.
4. **ZMQ server restart:** Toggling the ZMQ server checkbox in MM Options while a connection is open can cause timeouts rather than a clean restart. Workaround: restart MM if the connection breaks.

### Suitability as AI agent backend

**Excellent.** The `Core` object exposes clean Python functions (`snap_image()`, `set_property()`, `set_xy_position()`, etc.) that map directly to LLM tool definitions. The hooks and image processor architecture support feedback loops where the AI decides next steps based on what it sees.

---

## Option 2: Micro-Manager's Built-in Script Panel (BeanShell / Jython)

### What it is

Micro-Manager includes a Script Panel (Tools → Script Panel) that runs code inside the MM application. It supports two languages:
- **BeanShell:** Java-syntax interactive scripting. Has access to `mmc` (CMMCore), `gui` (Studio), and `acq` (acquisition engine) objects.
- **Jython:** Python 2.7 executed by the Jython runtime embedded in the MM JVM. Same access to `mmc`/`gui`/`acq`.

### Why it is not suitable

**No remote trigger mechanism.** The Script Panel is entirely GUI-driven — a human must manually open MM, navigate to the script panel, and click Run. There is no documented, supported way to trigger a script from an external Python process.

The only workarounds require fragile hacks: filesystem polling (have the Jython script watch for a command file, then execute it), or having the Jython script open its own socket server. Neither is reliable or practical.

**Python 2 only.** Jython hasn't released a stable Python 3 version. Modern AI/ML libraries (numpy, scikit-image, TensorFlow, PyTorch, OpenCV) are not available inside the Jython scripting environment.

**Blocks the GUI thread.** Long-running scripts freeze the MM interface.

**Verdict:** Useful for quick interactive scripting by an expert user. Not suitable as a backend for an AI agent that needs to be called programmatically.

---

## Option 3: Py4J — Python-Java Bridge

### What it is

Py4J (www.py4j.org) is a general-purpose library that creates a TCP-socket bridge between Python and a running Java process. The Java process starts a `GatewayServer`; Python connects to it and calls Java methods as if they were Python functions.

**Critical requirement:** The Java process must explicitly start a Py4J `GatewayServer`. You cannot connect to an arbitrary running JVM.

### The only known MM implementation: mm2python

The project `mm2python` (originally by Bryan Chhun at CZ Biohub) implemented Py4J for Micro-Manager as a MM 2.0 Java plugin. The plugin registered with MMStudio, started a Py4J GatewayServer exposing `CMMCore` and `Studio`, and separately used memory-mapped files (mmap) for high-bandwidth image transfer.

**Status: Archived October 30, 2022. No longer maintained.** It was superseded by pycro-manager. The repository note explicitly states it is no longer maintained.

### Could a new Py4J plugin be built?

Yes, but it would require substantial Java development:
1. Set up a Java/Maven build environment
2. Write a MM plugin implementing `MMPlugin` that starts a Py4J `GatewayServer` and exposes the desired API
3. Package as a JAR, deploy to MM's `mmplugins/` directory
4. Write matching Python client code

This is a significant engineering project. Pycro-Manager already does this better using ZeroMQ (which has better concurrency primitives than Py4J's TCP sockets).

**Verdict:** Technically feasible to build from scratch, but impractical when pycro-manager already exists and is actively maintained.

---

## Option 4: JPype — Embed a JVM Inside Python

### What it is

JPype (jpype.readthedocs.io) starts a full JVM directly inside the Python process via JNI (Java Native Interface). Python and Java share the same memory space. You can import Java classes and call Java methods with near-zero overhead (no socket round-trips).

### Critical clarification: JPype is NOT the same as pymmcore

The original report dismissed JPype by saying it is "functionally identical to pymmcore." This is wrong in a way that matters for this project.

- **pymmcore** wraps only the **C++ `MMCore` library** via SWIG. It gives you raw hardware control but has no Java layer at all. MMStudio, the Java plugin ecosystem, and Java-based plugins like Micro-Magellan simply don't exist from pymmcore's perspective.
- **JPype** starts a real **JVM** and can load any Java JARs, including the full MMStudio Java codebase. If you load the MM Java JARs into JPype, you can instantiate `MMStudio` itself, access the `Studio` interface, interact with installed Java plugins, and call any Java API in the MM ecosystem.

This is a fundamental distinction. JPype can reach what pymmcore cannot: the **Java plugin layer**.

### GUI mode: JPype can launch the full MM GUI from within Python

This is the most important clarification about JPype. Because JPype embeds a real JVM inside the Python process, that JVM can run **any** Java code — including Swing GUI applications. When `MMStudio` is instantiated in standalone mode, it starts MM's full Swing interface, which appears on screen as a normal window. The biologist sees and can interact with the same MM GUI they are used to.

An MM installation contains a full set of Java JARs (in the `jars/` subdirectory) and the native device adapter shared libraries (`.dll` / `.so` / `.dylib` in the main installation folder). JPype can:

1. Start a JVM with those JARs on the classpath and the device adapter directory on `java.library.path`
2. Instantiate `org.micromanager.internal.MMStudio` in standard (GUI) mode
3. The MM Swing GUI appears on screen — the biologist sees the full MM interface
4. Python holds a direct reference to the `Studio` and `Core` objects
5. Every agent command (snap, stage move, live view, property change) calls a Java method directly in the same JVM; the GUI reflects the change immediately, with zero IPC overhead

```python
import glob
import jpype
import jpype.imports

mm_path = "/Applications/Micro-Manager2.0"
jars = glob.glob(f"{mm_path}/jars/*.jar")

jpype.startJVM(
    f"-Djava.library.path={mm_path}",  # device adapters (.dylib / .dll / .so)
    classpath=jars,
    convertStrings=False
)

from org.micromanager.internal import MMStudio

# Launch MM in standard GUI mode — the MM window appears on screen
studio = MMStudio(False)   # False = standalone (with GUI); True = as plugin (reduced init)
core = studio.core()

# Load hardware configuration exactly as MM does on startup
core.loadSystemConfiguration(f"{mm_path}/MMConfig.cfg")

# Agent tool functions: direct JNI calls, no sockets, GUI responds immediately
core.snapImage()
img = core.getImage()          # numpy array (with additional conversion)
studio.live().setLiveModeOn(True)   # MM's camera preview window opens
```

**What the biologist sees:** the full MM GUI window, exactly as if they had double-clicked the MM application. When the agent calls `setLiveModeOn(True)`, the live view window opens. When the agent sets a device property, the Device Property Browser updates. When an acquisition runs, MM's viewer opens and fills with images. The biologist can interact with the MM GUI directly at the same time.

**The critical difference from pycro-manager:** with pycro-manager, the biologist opens MM themselves first, and the agent connects to the running application. With JPype, the agent (Python) starts MM from within itself. The biologist cannot run MM independently from the agent — MM only runs while Python is running. This inverts the workflow: the biologist must start the agent first, which then launches MM, rather than opening MM as they normally would and separately opening the agent.

Because there is no IPC layer, every Python call to a Java method is a direct JNI call into the same process. No serialization, no sockets, no version-synchronization problem between a Python package and a running MM application.

### Access to Java plugins

This is the key advantage over both pymmcore and over pycro-manager's headless mode. With JPype you can do:

```python
# Load a Java plugin class that lives in the MM plugins directory
MagellanPlugin = jpype.JClass("org.micromanager.magellan.main.Magellan")
magellan = MagellanPlugin()
# Call plugin Java API directly
```

No ZMQ bridge needs to support the plugin's API. If the class is on the classpath, you can call it.

### Limitations and challenges

1. **Classpath complexity.** An MM installation has dozens of JARs. You need to discover and pass all of them to `startJVM`. This is solvable with `glob`, but the exact set of required JARs must be determined empirically and may vary between MM versions.

2. **Native device adapter loading.** MM device adapters are native shared libraries (`.dll` / `.so` / `.dylib`). The JVM loads them via `System.loadLibrary()`, which searches `java.library.path`. This must be set to the MM installation directory. JPype supports this via the `-Djava.library.path=...` JVM argument passed to `startJVM`.

3. **MMStudio constructor arguments need verification.** The exact signature and semantics of `MMStudio`'s constructor are not externally documented as a stable API. `MMStudio(false)` (standalone/GUI mode) and `MMStudio(true)` (plugin/reduced-init mode) are the two variants, but whether true headless operation (no display at all) is supported depends on MM version and OS. GUI mode is more reliable since it follows the standard MM startup path. The exact arguments should be verified against the MM source for the target version.

4. **Agent controls MM's lifecycle, not the biologist.** Because JPype starts MM from within the Python process, MM only runs while the agent is running. The biologist cannot open MM independently and then attach the agent — they must start the agent first. If the agent needs to be restarted, MM closes and reopens. This is a workflow difference from pycro-manager, where MM runs independently.

5. **One JVM per process; crashes are shared.** JPype starts a JVM that cannot be stopped and restarted within the same Python process. If a misbehaving device adapter causes a JVM crash, the Python process — and the agent — crash with it. With pycro-manager, MM is a separate process; Python survives a MM crash and can reconnect.

6. **Thread safety for Swing calls.** Hardware commands (`core.*`) are thread-safe in MM and can be called from any Python thread via JPype. Calls that touch Swing GUI components should be posted to Java's Event Dispatch Thread (EDT). For the agent's primary use case (hardware control), this is rarely a concern in practice, but GUI-manipulating calls need care.

7. **No existing implementation to copy.** Unlike pycro-manager (which is mature and documented), there is no existing JPype+MMStudio integration to reference. This approach would need to be built from scratch with significant trial-and-error to resolve classpath, native library, and initialization issues.

### Comparison with pycro-manager

| Factor | JPype | Pycro-Manager |
|---|---|---|
| MM GUI visible to biologist | Yes — GUI launched from within Python | Yes — connects to independently running MM |
| Who starts MM | The agent (Python) starts MM | The biologist opens MM; agent connects to it |
| Biologist can run MM without agent | No | Yes |
| Access Java MM plugins | Yes (any class on classpath) | Yes (via ZMQ bridge, limited by serialization) |
| IPC overhead | None (same process, direct JNI) | ZMQ socket round-trips |
| Version sync required | No | Yes (Python pkg must match MM ZMQ server) |
| If MM crashes... | Python process crashes too | Python survives, can reconnect |
| GUI mode support | Yes (standard MM startup path) | Yes (connects to running MM) |
| Headless mode support | Uncertain (needs testing) | Yes (documented, tested) |
| Documentation / examples | None for MM specifically | Extensive |
| Maintenance burden | High (build from scratch) | Low (use existing library) |

### Suitability as AI agent backend

**Technically viable in GUI mode and offers distinct architectural advantages, but requires substantial pioneering work.**

JPype with GUI mode is a genuine alternative to pycro-manager. The biologist would see the full MM interface and watch it respond to agent commands exactly as with pycro-manager. The in-process JNI approach eliminates IPC overhead entirely and gives unrestricted access to every Java class — including plugins that pycro-manager's ZMQ bridge may not expose cleanly.

The primary workflow difference from pycro-manager is ownership: the agent starts MM, not the biologist. This is a meaningful ergonomic downside. A biologist who is used to opening MM independently, loading a configuration, and then starting an experiment would need to change their workflow to always start via the agent.

No reference implementation exists. The developer would need to solve classpath assembly, native library path configuration, and constructor argument semantics before writing a single line of agent logic. Estimated effort: several weeks of integration work.

**Verdict:** A genuine alternative with important advantages — no IPC, no version sync, unrestricted Java plugin access, full GUI visible. The cost is that the agent owns MM's lifecycle rather than the biologist, and there is no existing implementation to build on. Worth pursuing if pycro-manager's plugin access proves insufficient or if the ZMQ overhead becomes a practical problem.

---

## Option 5: REST or HTTP API — MMrestServer

### Does one exist?

**Yes.** The project **MMrestServer** (https://github.com/kbellve/MMrestServer), by Karl Bellve at UMass Medical School, is a MM 2.0 plugin that embeds an HTTP server directly in the running Micro-Manager application and exposes a REST API on port 8000.

**Last commit: December 11, 2017.** The repository has 64 commits; all development was by a single author (kbellve) between August and December 2017. It is **unmaintained**.

### How it works

MMrestServer is installed exactly like any other MM 2.0 plugin: download `µmWeb.jar` from the `/dist/` folder in the repository and place it in MM's `mmplugins/` directory. After restarting MM, activate it via Plugins → Beta → **µmWeb**. The plugin then starts an HTTP server using Java's built-in `com.sun.net.httpserver.HttpServer` on port 8000. No external HTTP framework is required; it ships with its own.

Because it runs as a loaded plugin inside the MM application, the **full MM GUI is running** during its operation. The biologist sees and can interact with the MM interface normally while the agent sends HTTP requests to the server.

### Endpoints

The server exposes the following routes (all require query parameters passed as URL parameters):

| Endpoint | Operation |
|---|---|
| `GET /` | Index / built-in documentation page |
| `GET /snap/image/` | Snap a single image |
| `GET /get/image/` | Retrieve the last acquired image |
| `GET /copy/image/` | Copy the current image |
| `GET /get/busy/` | Check whether MM hardware is busy |
| `GET /get/property/` | Get a device property (one or all properties for a device) |
| `GET /set/property/` | Set a device property |
| `GET /set/position/` | Set a device position |
| `GET /set/ROI/` | Set the camera region of interest |
| `GET /get/acquisition/` | Check whether an acquisition is running |
| `GET /run/acquisition/` | Start an acquisition |
| `GET /run/script/` | Execute a BeanShell script |

Responses are JSON. For example, `/get/property/` with parameters `device=Camera&property=Exposure` returns a JSON object with `status: "OK"` and the property value.

### What it does NOT cover

The API surface is intentionally minimal. There is no endpoint for:
- Starting or stopping live view
- Z-stack or multi-dimensional acquisition configuration (acquisitions are triggered but parameters must be pre-set in MM)
- Config group / hardware preset management
- Autofocus
- Stage position lists or multi-position setups
- Any Java plugin API (Micro-Magellan, etc.)
- Raw image data in formats suitable for numpy (image endpoints likely return PNG or similar)

### Compatibility with current MM 2.0

**Unknown and risky.** The last commit targets "µManager 2.0 code base" as it existed in late 2017. Micro-Manager 2.0 has continued significant development since then, and the MM 2.0 Java API has changed. The plugin may or may not load cleanly against a current MM nightly build. The README itself notes the code "may or may not work at any time due to ongoing development" — a warning that was written when the author was actively updating it, making compatibility against today's MM even less certain. Testing against the current MM version would be required before relying on it.

### Suitability as AI agent backend

**Conceptually ideal, practically uncertain.** REST over HTTP is the cleanest possible interface for an LLM agent: every endpoint maps directly to a tool definition, `requests.get(...)` is all the Python needed, no special libraries are required, and the design is completely transparent. The GUI stays visible and responds to commands.

The problems are: the API surface is small (missing live view, config groups, Z-stack configuration, and plugin access), compatibility with current MM is unverified, the code is 8 years old with no maintainer, and bugs cannot be fixed. For a one-off research setup where the supported operations happen to cover the needed workflow, it could work. For a production AI agent intended to support arbitrary acquisition protocols, the gaps are significant.

**Verdict:** A real, working REST plugin for MM 2.0 exists but is unmaintained since 2017. Its limited endpoint set and unknown compatibility with current MM make it unsuitable as the primary backend for a general-purpose agent. However, it establishes proof-of-concept that a REST approach is architecturally viable and straightforward to call from Python.

---

## Option 6: ZeroMQ Directly (Without Pycro-Manager)

### What it is

The ZMQ server embedded in MM 2.0 uses a proprietary JSON-based RPC protocol called PyJavaZ, implemented in `org.micromanager.internal.zmq.ZMQServer`. Messages specify a class path, method name, argument types, arguments, and object identifiers. Image data is transferred over separate PUSH/PULL sockets as raw bytes.

### Can it be used without pycro-manager?

**Technically yes, but practically pointless.** The protocol is implemented in the PyJavaZ library (the Python side of pycro-manager). Reimplementing it from scratch would give you exactly what pycro-manager already provides, with significant reverse-engineering effort.

Importantly, you can use pycro-manager's lower-level layers without using its high-level Acquisition engine. The `Core()` and `Studio()` objects give direct access to the ZMQ-proxied Java API without requiring the acquisition machinery.

**Verdict:** Use pycro-manager instead of reimplementing the protocol.

---

## Comparison Table

| Approach | Maintained | GUI visible to biologist | Headless (optional) | Complexity | AI Agent Ready |
|---|---|---|---|---|---|
| **Pycro-Manager** | Yes (active) | **Yes — GUI responds live to agent commands** | Yes | Low | **Excellent** |
| Script Panel (Jython) | Yes (part of MM) | GUI runs, but agent cannot trigger scripts remotely | No | Very High (no remote trigger) | Not suitable |
| Py4J + custom plugin | Would need to be built | Yes | Yes | Very High (Java dev required) | Possible but impractical |
| mm2python (Py4J) | No (archived 2022) | Yes | Limited | Medium | Not recommended |
| JPype + MM Java JARs | Yes (general library) | Yes — full MM GUI launched from within Python | Uncertain | Very High (no existing implementation) | Possible; GUI visible; no IPC; unrestricted plugin access; agent owns MM lifecycle |
| MMrestServer (REST) | No (last commit Dec 2017) | Yes — plugin runs inside MM GUI | No | Low (install JAR, enable plugin) | Conceptually ideal; limited endpoints; unknown compatibility with current MM |
| ZMQ directly | Same as pycro-manager | Yes | Yes | Very High | Use pycro-manager instead |

---

## Recommendation: Pycro-Manager as the AI Agent Backend

Pycro-Manager is the clear choice. The recommended architecture has two modes, with the GUI-visible mode as the primary day-to-day experience.

---

### Primary mode: Agent + running MM GUI

The biologist's workflow:
1. Open Micro-Manager normally and load their hardware configuration.
2. Open the agent chat interface (a separate window — a terminal, web UI, or desktop app).
3. Type a natural language request: *"Start live view, set the 488 laser to 30%, and find the focal plane."*
4. Watch MM respond: the camera preview window opens, laser power updates, autofocus runs.
5. Type the next request: *"Take a 5-position Z-stack from −10 µm to +10 µm in 2 µm steps, save to /data/today."*
6. Watch MM's acquisition viewer open, images appear slice by slice, progress indicator advances.
7. Continue interacting with either the agent or the MM GUI directly.

**Agent code for this mode:**

```python
from pycromanager import Core, Studio

# Connect to the already-running MM application (GUI is open)
core = Core()
studio = Studio()

# These become the LLM's tool functions:

def start_live_view():
    studio.live().set_live_mode_on(True)
    # MM's camera preview window opens immediately

def stop_live_view():
    studio.live().set_live_mode_on(False)

def snap_and_show():
    studio.live().snap(True)
    # Image appears in MM's snap/live window

def set_exposure(ms: float):
    core.set_exposure(ms)

def set_channel(preset_name: str):
    core.set_config("Channel", preset_name)
    # Device Property Browser reflects the change

def move_stage_xy(x_um: float, y_um: float):
    core.set_xy_position(x_um, y_um)
    core.wait_for_device(core.get_xy_stage_device())
    # MM stage position display updates

def move_stage_z(z_um: float):
    core.set_position(z_um)
    core.wait_for_device(core.get_focus_device())

def set_device_property(device: str, prop: str, value: str):
    core.set_property(device, prop, value)

def run_zstack(z_start: float, z_end: float, z_step: float,
               channel: str, exposure_ms: float, save_path: str):
    from pycromanager import Acquisition, multi_d_acquisition_events
    events = multi_d_acquisition_events(
        z_start=z_start, z_end=z_end, z_step=z_step,
        channel_group="Channel", channels=[channel],
        channel_exposures_ms=[exposure_ms]
    )
    with Acquisition(directory=save_path, name="zstack") as acq:
        acq.acquire(events)
    # MM's acquisition viewer opens and images appear as they are captured
```

The crucial point: **none of this requires any special "GUI mode" flag.** When pycro-manager connects to an MM application that has a GUI, all commands automatically execute through and are reflected in that GUI. The biologist sees exactly the same views they would see if they had clicked the buttons themselves.

---

### Secondary mode: Headless operation for verified protocols

Once the biologist has confirmed a protocol works correctly — they have watched it execute in the GUI and are satisfied with the results — the same code can be run unattended without a GUI. This is useful for overnight acquisitions, scheduled batch runs, or running the microscope from a remote machine.

```python
from pycromanager import start_headless, Core, Studio

# Launch MM without a GUI (same JAR files, same device adapters)
start_headless(
    mm_app_path="/Applications/Micro-Manager2.0",
    config_file="/path/to/MMConfig.cfg"
)

# All the same tool functions work identically
core = Core()
studio = Studio()
```

The code is identical to the GUI-visible mode. Nothing about the tool functions or agent logic changes between modes.

---

### Workflow summary for a biologist

| Stage | Mode | What the biologist does |
|---|---|---|
| Learning / exploring | GUI visible | Opens MM, chats with agent, watches GUI respond |
| Verifying a new protocol | GUI visible | Runs protocol once, inspects images and stage movements |
| Routine established protocol | Headless (optional) | Agent runs MM in background; biologist checks saved images afterward |

---

## References

- [Pycro-Manager GitHub](https://github.com/micro-manager/pycro-manager)
- [Pycro-Manager Documentation](https://pycro-manager.readthedocs.io)
- [Pycro-Manager Nature Methods Paper (PMID 33674797)](https://pubmed.ncbi.nlm.nih.gov/33674797/)
- [mmpycorex (headless MM backend)](https://github.com/micro-manager/mmpycorex)
- [PyJavaZ (ZMQ Java-Python bridge)](https://github.com/PyJavaZ/PyJavaZ)
- [AcqEngJ (Java acquisition engine)](https://github.com/micro-manager/AcqEngJ)
- [mm2python (archived Py4J plugin)](https://github.com/czbiohub-sf/mm2python)
- [MMrestServer (unmaintained REST plugin, last updated Dec 2017)](https://github.com/kbellve/MMrestServer)
- [Micro-Manager Script Panel](https://micro-manager.org/Script_Panel_GUI)
- [Micro-Manager Version 2.0 API](https://micro-manager.org/Version_2.0_API)
- [Micro-Manager Programming Guide](https://micro-manager.org/Micro-Manager_Programming_Guide)
- [CMMCore Javadoc](https://javadoc.scijava.org/Micro-Manager-Core/mmcorej/CMMCore.html)
- [JPype Documentation](https://jpype.readthedocs.io)
- [Smart Microscopy Review (npj Imaging 2025)](https://www.nature.com/articles/s44303-026-00145-y)
- [Py4J Documentation](https://www.py4j.org)
