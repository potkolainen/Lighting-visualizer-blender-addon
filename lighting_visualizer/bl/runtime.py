"""Runtime glue: the shared DMX engine, network receiver, fixture library and the timer that drives the scene."""
from __future__ import annotations

import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from ..core import evaluate as ev
from ..core.fixture import FixtureType
from ..core.library import Library
from ..core.net import packets
from ..core.net.dmx_engine import DmxEngine, EngineConfig, parse_universe_list, sacn_subscriptions
from ..core.net.receiver import NetworkReceiver, ReceiverConfig, list_ipv4_interfaces

engine = DmxEngine()
receiver = NetworkReceiver(engine)
library = Library()
interfaces: list = [("0.0.0.0", "Any adapter (0.0.0.0)", "")]
fixtures: dict = {}                     # object name -> FixtureRT
_flags = {"refresh_all": True, "rebuild": True}
_timing = {"rebuild": 0.0, "redraw": 0.0, "last": 0.0}


@dataclass
class FixtureRT:
    obj: bpy.types.Object
    json: str
    ftype: FixtureType
    mode_name: str
    state: object = None
    last_key: tuple = ()
    gobo_angle: float = 0.0
    prism_angle: float = 0.0
    last_t: float = 0.0
    warnings: list = field(default_factory=list)


def user_dir(name: str) -> Path:
    pkg = ".".join(__name__.split(".")[:-2])
    try:
        return Path(bpy.utils.extension_path_user(pkg, path=name, create=True))
    except Exception:
        return Path(bpy.utils.user_resource("DATAFILES", path=f"lighting_visualizer/{name}", create=True))


def init_library():
    library.user_dir = user_dir("library")
    library.cache_dir = user_dir("gdtf_bundled")
    library.reload()


def refresh_interfaces():
    interfaces[:] = [("0.0.0.0", "Any adapter (0.0.0.0)", "")] + [(ip, label, "") for label, ip in list_ipv4_interfaces()]


def request_refresh():
    _flags["refresh_all"] = True


def request_rebuild():
    _flags["rebuild"] = True
    _flags["refresh_all"] = True


def resolve_type(ob) -> FixtureType | None:
    text = ob.lv.type_json
    if text:
        try:
            return FixtureType.from_json(text)
        except (ValueError, KeyError):
            return None
    return library.get(ob.lv.type_id)


def engine_config(lv) -> EngineConfig:
    mapping = {(m.protocol, m.source): m.target for m in lv.mapping}
    return EngineConfig(
        universes=parse_universe_list(lv.universes), mapping=mapping, artnet_offset=lv.artnet_offset,
        loss_policy=lv.loss_policy)


# Network: the receiver follows the scene settings by itself (see tick), there is no start button.
SACN_PORT, ARTNET_PORT = packets.SACN_PORT, packets.ARTNET_PORT
RETRY_SECONDS = 3.0
_net = {"sockets": None, "multicast": None, "retry_at": 0.0}
_fixture_universes: set = set()


def _reconcile_network(lv, now: float):
    if not lv.listen:
        if receiver.running:
            receiver.stop()
            _net.update(sockets=None, multicast=None)
        return
    cfg = engine_config(lv)
    if engine.config != cfg:
        engine.configure(cfg)
    sockets = (lv.protocol, lv.interface, lv.multicast)
    if not receiver.running or sockets != _net["sockets"]:
        if now < _net["retry_at"]:
            return
        receiver.start(ReceiverConfig(protocol=lv.protocol, interface_ip=lv.interface, multicast=lv.multicast,
                                      sacn_port=SACN_PORT, artnet_port=ARTNET_PORT, engine=cfg))
        _net.update(sockets=sockets, multicast=None, retry_at=0.0 if receiver.running else now + RETRY_SECONDS)
        request_refresh()
    if receiver.running:
        # With no explicit universe list, follow the universes the lamps are patched to.
        wanted = cfg.universes or frozenset(_fixture_universes or {1})
        subs = sacn_subscriptions(EngineConfig(universes=wanted, mapping=cfg.mapping))
        if subs != _net["multicast"]:
            receiver.update_multicast(subs)
            _net["multicast"] = subs


def lamp_summary(rt) -> tuple:
    """(text, icon) describing what a lamp is doing with the data it receives."""
    fx, st = rt.obj.lv, rt.state
    if fx.use_test:
        return "test mode (sliders)", "PLAY"
    if fx.universe not in engine.active_universes():
        return f"no data on universe {fx.universe}", "RADIOBUT_OFF"
    if st is None:
        return "waiting", "TIME"
    parts = []
    if rt.ftype.has_attribute("Pan"):
        parts.append(f"pan {st.pan:.0f}\u00b0 tilt {st.tilt:.0f}\u00b0")
    reason = st.dark_reason()
    parts.insert(0, f"dark: {reason}" if reason else f"{st.intensity * 100:.0f}%")
    return "  ".join(parts), "OUTLINER_OB_LIGHT" if not reason else "LIGHT_DATA"


# ---------------------------------------------------------------------------------------------------------------
def _rebuild(scene):
    old = dict(fixtures)
    fixtures.clear()
    for ob in scene.objects:
        if not ob.lv.is_fixture:
            continue
        text = ob.lv.type_json
        prev = old.get(ob.name)
        if prev is not None and prev.obj is ob and prev.json == text and prev.mode_name == ob.lv.mode:
            fixtures[ob.name] = prev
            continue
        ftype = resolve_type(ob)
        if ftype is not None and ftype.modes:
            fixtures[ob.name] = FixtureRT(ob, text, ftype, ob.lv.mode)
    _fixture_universes.clear()
    _fixture_universes.update(rt.obj.lv.universe for rt in fixtures.values())


def _redraw():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _update_fixture(rt, changed, refresh_all, now, exposure) -> bool:
    from . import build
    fx = rt.obj.lv
    mode = rt.ftype.mode(rt.mode_name)
    animated = rt.state is not None and rt.state.animated
    first = rt.state is None
    if fx.use_test:
        if not (refresh_all or animated or first):
            return False
        data, address = bytes(fx.test_values), 1
    else:
        if not (fx.universe in changed or refresh_all or animated or first):
            return False
        data, address = engine.universe_data(fx.universe), fx.address
    rt.state = ev.evaluate(rt.ftype, mode, data, address)
    dt = now - rt.last_t if rt.last_t else 0.0
    rt.last_t = now
    rt.gobo_angle = (rt.gobo_angle + rt.state.gobo_rot_dps * dt) % 360.0
    rt.prism_angle = (rt.prism_angle + rt.state.prism_rot_dps * dt) % 360.0
    build.apply_state(rt, now, exposure)
    return True


def tick():
    try:
        scene = bpy.context.scene
        if scene is None:
            return 0.5
        lv = scene.lv
        now = time.monotonic()
        _reconcile_network(lv, now)
        receiver.poll()
        engine.expire()
        changed = engine.take_changed()
        if _flags["rebuild"] or now - _timing["rebuild"] > 1.0:
            _rebuild(scene)
            _flags["rebuild"] = False
            _timing["rebuild"] = now
        refresh_all = _flags["refresh_all"]
        _flags["refresh_all"] = False
        busy = bool(changed) or refresh_all
        for rt in fixtures.values():
            try:
                busy = _update_fixture(rt, changed, refresh_all, now, lv.exposure) or busy
            except ReferenceError:  # object deleted since the last rebuild
                _flags["rebuild"] = True
        if busy or now - _timing["redraw"] > 1.0:
            _timing["redraw"] = now
            _redraw()
        return 1.0 / max(lv.refresh_hz, 1)
    except Exception:
        traceback.print_exc()
        return 1.0


def start_timer():
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.5, persistent=True)


def stop_timer():
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    receiver.stop()
