import socket
import sys
import time
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from lighting_visualizer.core import gdtf, mvr, optics, qlc  # noqa: E402
from sample_files import MVR_XML, make_gdtf, make_qxf  # noqa: E402
from lighting_visualizer.core.evaluate import evaluate  # noqa: E402
from lighting_visualizer.core.fixture import FixtureType  # noqa: E402
from lighting_visualizer.core.library import Library  # noqa: E402
from lighting_visualizer.core.net import packets  # noqa: E402
from lighting_visualizer.core.net.dmx_engine import (DmxEngine, EngineConfig, parse_universe_list,  # noqa: E402
                                                     sacn_subscriptions)
from lighting_visualizer.core.net.receiver import NetworkReceiver, ReceiverConfig  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


# ---- packets ---------------------------------------------------------------------------------------------------
def test_sacn_roundtrip():
    data = bytes(range(200))
    p = packets.parse_sacn(packets.build_sacn(7, data, sequence=5, priority=120, source_name="MA3"))
    assert (p.universe, p.data, p.sequence, p.priority, p.source_name) == (7, data, 5, 120, "MA3")
    assert not p.preview and not p.terminated


def test_sacn_flags_and_sync():
    p = packets.parse_sacn(packets.build_sacn(1, b"\x01", preview=True, terminated=True, sync_address=9))
    assert p.preview and p.terminated and p.sync_address == 9
    s = packets.parse_sacn(packets.build_sacn_sync(9, sequence=3))
    assert isinstance(s, packets.SyncPacket) and s.sync_address == 9


def test_sacn_rejects_garbage_and_alternate_start_code():
    assert packets.parse_sacn(b"hello") is None
    raw = bytearray(packets.build_sacn(1, b"\x10\x20"))
    raw[125] = 0xDD
    assert packets.parse_sacn(bytes(raw)) is None


def test_artnet_roundtrip():
    p = packets.parse_artnet(packets.build_artnet(0x0102, bytes([1, 2, 3, 4]), sequence=9))
    assert (p.universe, p.data, p.sequence, p.protocol) == (0x0102, bytes([1, 2, 3, 4]), 9, "artnet")
    assert isinstance(packets.parse_artnet(packets.build_artnet_sync()), packets.SyncPacket)
    assert packets.parse_artnet(b"Art-Net\x00\x00\x20" + bytes(20)) is None


def test_multicast_group():
    assert packets.sacn_multicast_group(1) == "239.255.0.1"
    assert packets.sacn_multicast_group(300) == "239.255.1.44"


# ---- engine ----------------------------------------------------------------------------------------------------
def make_engine(**cfg):
    clock = Clock()
    e = DmxEngine(clock)
    e.configure(EngineConfig(**cfg))
    return e, clock


def sacn(universe, data, seq=0, prio=100, cid=b"A" * 16, **kw):
    return packets.parse_sacn(packets.build_sacn(universe, bytes(data), seq, prio, cid=cid, **kw))


def test_artnet_offset_and_change_detection():
    e, _ = make_engine()
    e.ingest(packets.parse_artnet(packets.build_artnet(0, bytes([255, 0]))), "10.0.0.5")
    assert e.take_changed() == {1: bytes([255, 0]) + bytes(510)}
    e.ingest(packets.parse_artnet(packets.build_artnet(0, bytes([255, 0]))), "10.0.0.5")
    assert e.take_changed() == {}


def test_priority_and_htp_merge():
    e, _ = make_engine()
    e.ingest(sacn(1, [10, 200], cid=b"A" * 16, prio=100))
    e.ingest(sacn(1, [50, 20], cid=b"B" * 16, prio=100))
    assert e.universe_data(1)[:2] == bytes([50, 200])
    e.ingest(sacn(1, [1, 1], cid=b"C" * 16, prio=150))
    assert e.universe_data(1)[:2] == bytes([1, 1])


def test_sequence_validation():
    e, _ = make_engine()
    e.ingest(sacn(1, [1], seq=10), "1.1.1.1")
    e.ingest(sacn(1, [2], seq=12), "1.1.1.1")           # one packet lost
    e.ingest(sacn(1, [3], seq=11), "1.1.1.1")           # out of order -> discarded
    assert e.universe_data(1)[0] == 2
    info = e.sources()[0]
    assert info.seq_lost == 1 and info.seq_discarded == 1
    e.ingest(sacn(1, [4], seq=200), "1.1.1.1")          # large jump is accepted (source restarted)
    assert e.universe_data(1)[0] == 4


def test_source_timeout_hold_and_blackout():
    for policy, expected in (("hold", 99), ("blackout", 0)):
        e, clock = make_engine(loss_policy=policy)
        e.ingest(sacn(1, [99]), "1.1.1.1")
        e.take_changed()
        assert e.status() == "receiving"
        clock.t += 3.0
        e.expire()
        assert e.status() == "lost"
        assert e.universe_data(1)[0] == expected
        assert (1 in e.take_changed()) == (policy == "blackout")
        assert e.sources() == []


def test_stream_terminated_removes_source():
    e, _ = make_engine(loss_policy="blackout")
    e.ingest(sacn(1, [99]))
    e.ingest(sacn(1, [99], seq=1, terminated=True))
    assert e.universe_data(1)[0] == 0


def test_preview_ignored_and_universe_filter_and_mapping():
    e, _ = make_engine(universes=frozenset({5}), mapping={("sacn", 1): 5})
    e.ingest(sacn(1, [7], preview=True))
    assert e.active_universes() == []
    e.ingest(sacn(1, [7]))
    e.ingest(sacn(2, [8]))
    assert e.active_universes() == [5]
    assert e.universe_data(5)[0] == 7


def test_sacn_sync_holds_until_sync():
    e, _ = make_engine()
    e.ingest(sacn(1, [42], sync_address=100))
    assert e.universe_data(1)[0] == 0
    e.ingest(packets.parse_sacn(packets.build_sacn_sync(100)))
    assert e.universe_data(1)[0] == 42


def test_artnet_sync_holds_until_artsync_then_times_out():
    e, clock = make_engine()
    e.ingest(packets.parse_artnet(packets.build_artnet(0, bytes([1]))), "1.1.1.1")
    assert e.universe_data(1)[0] == 1                       # immediate mode until the first ArtSync
    e.ingest(packets.parse_artnet(packets.build_artnet_sync()), "1.1.1.1")
    e.ingest(packets.parse_artnet(packets.build_artnet(0, bytes([9]), sequence=2)), "1.1.1.1")
    assert e.universe_data(1)[0] == 1
    e.ingest(packets.parse_artnet(packets.build_artnet_sync()), "1.1.1.1")
    assert e.universe_data(1)[0] == 9
    clock.t += 5.0                                          # no ArtSync for > 4 s -> immediate again
    e.ingest(packets.parse_artnet(packets.build_artnet(0, bytes([7]), sequence=3)), "1.1.1.1")
    assert e.universe_data(1)[0] == 7


def test_ignored_universes_and_protocol_specific_signal_loss():
    e, clock = make_engine(universes=frozenset({1}))
    e.ingest(sacn(5, [1]))
    e.ingest(packets.parse_artnet(packets.build_artnet(2, bytes([1]))), "1.1.1.1")     # internal universe 3
    assert e.ignored_universes() == [("artnet", 2), ("sacn", 5)] and e.active_universes() == []
    e.configure(EngineConfig())
    e.ingest(packets.parse_artnet(packets.build_artnet(0, bytes([9]), sequence=1)), "1.1.1.1")
    clock.t += 5.0
    assert e.status() == "receiving"                          # Art-Net senders may stay quiet for several seconds
    e.expire()
    assert e.universe_data(1)[0] == 9
    clock.t += 6.0
    assert e.status() == "lost"


def test_universe_list_and_subscriptions():
    assert parse_universe_list("1-3, 7;x") == frozenset({1, 2, 3, 7})
    cfg = EngineConfig(universes=frozenset({1, 2, 9}), mapping={("sacn", 20): 9})
    assert sacn_subscriptions(cfg) == [1, 2, 20]


# ---- receiver over loopback ------------------------------------------------------------------------------------
def wait_for(cond, timeout=2.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


def test_receiver_loopback_sacn_and_artnet():
    engine = DmxEngine()
    rx = NetworkReceiver(engine)
    rx.start(ReceiverConfig(sacn_port=0, artnet_port=0, multicast=False))
    try:
        assert not rx.errors and rx.running
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.sendto(packets.build_sacn(3, bytes([11, 22])), ("127.0.0.1", rx.ports["sacn"]))
        tx.sendto(packets.build_artnet(0, bytes([33, 44])), ("127.0.0.1", rx.ports["artnet"]))
        assert wait_for(lambda: rx.poll() >= 0 and set(engine.active_universes()) == {1, 3})
        assert engine.universe_data(3)[:2] == bytes([11, 22])
        assert engine.universe_data(1)[:2] == bytes([33, 44])
        assert engine.status() == "receiving"
        assert engine.last_source_ip == "127.0.0.1"
    finally:
        rx.stop()
    assert not rx.running


def test_receiver_protocol_selection():
    rx = NetworkReceiver(DmxEngine())
    rx.start(ReceiverConfig(protocol="artnet", sacn_port=0, artnet_port=0, multicast=False))
    try:
        assert set(rx.ports) == {"artnet"}
    finally:
        rx.stop()


# ---- fixture evaluation ----------------------------------------------------------------------------------------
LIB = Library()


def state_for(fid, mode, dmx, address=1):
    ft = LIB.get(fid)
    data = bytearray(512)
    for i, v in enumerate(dmx):
        data[address - 1 + i] = v
    return evaluate(ft, ft.mode(mode), bytes(data), address), ft


def test_moving_head_pan_tilt_dimmer_and_zoom():
    st, ft = state_for("generic.mh_spot", "Standard", [128, 0, 255, 255, 255, 128, 0, 255, 0, 0, 0, 0, 0, 0] + [0] * 8)
    # pan 16-bit centre -> ~0 degrees, tilt full -> +half range, dimmer full
    assert abs(st.pan) < 1.0
    assert st.tilt == pytest.approx(135.0, abs=0.6)
    assert st.intensity == pytest.approx(1.0)
    assert st.beam_angle == pytest.approx(10.0)             # zoom 0 -> narrowest


def test_dimmer_rgb_and_default_channels():
    st, _ = state_for("generic.led_par", "Dimmer + RGB 4ch", [255, 255, 0, 0])
    assert st.rgb == (1.0, 0.0, 0.0) and st.intensity == 1.0
    st, _ = state_for("generic.led_par", "RGB 3ch", [0, 0, 128])
    assert st.intensity == 1.0 and st.rgb[2] == pytest.approx(128 / 255)


def test_address_offset_and_short_buffer_uses_defaults():
    st, _ = state_for("generic.led_par", "Dimmer + RGB 4ch", [64, 255, 255, 255], address=101)
    assert st.intensity == pytest.approx(64 / 255)
    ft = LIB.get("generic.led_par")
    short = evaluate(ft, ft.mode("Dimmer + RGB 4ch"), bytes(10), 20)   # channels fall outside -> defaults
    assert short.rgb == (1.0, 1.0, 1.0)


def test_shutter_and_strobe():
    st, _ = state_for("generic.mh_spot", "Standard", [128, 0, 128, 0, 255, 5])
    assert st.shutter_closed and st.shutter_factor(0.0) == 0.0
    st, _ = state_for("generic.mh_spot", "Standard", [128, 0, 128, 0, 255, 100])
    assert not st.shutter_closed and st.shutter_factor(0.0) == 1.0


def test_iris_narrows_beam_and_frost_softens():
    open_, ft = state_for("generic.mh_spot", "Standard", [0] * 22)
    closed, _ = state_for("generic.mh_spot", "Standard", [0] * 8 + [255] + [0] * 13)
    assert closed.iris == pytest.approx(0.1)
    assert closed.beam_angle < open_.beam_angle
    assert optics.edge_blend(20, 40, 1.0, 0.5) > optics.edge_blend(20, 40, 0.0, 0.5)


def test_cmy_and_cto():
    ft = LIB.get("generic.mh_spot")
    mode = ft.mode("Standard")
    data = bytearray(512)
    cto_off = mode.channel("CTO").offsets[0] - 1
    data[cto_off] = 255
    st = evaluate(ft, mode, bytes(data))
    assert st.rgb[2] < st.rgb[0]                       # warmer


def test_fixture_json_roundtrip():
    for t in LIB.all():
        assert FixtureType.from_json(t.to_json()).to_dict() == t.to_dict()


def test_library_contents_and_user_save(tmp_path):
    ids = {t.id for t in LIB.all()}
    assert {"etc.source_four_26", "generic.par64", "generic.cp61", "martin.minimac"} <= ids
    assert all(t.provenance["optical"] == "estimated" for t in LIB.all() if not t.id.startswith("gdtf.share."))
    lib = Library(tmp_path)
    t = FixtureType.from_json(LIB.get("generic.led_par").to_json())
    t.id, t.model = "tmp", "My PAR"
    path = lib.save_user(t)
    assert path.exists() and lib.get("user.generic_my_par").model == "My PAR"
    assert Library(tmp_path).is_user("user.generic_my_par")
    assert lib.delete_user("user.generic_my_par") and not path.exists()


# ---- optics ----------------------------------------------------------------------------------------------------
def test_optics_basics():
    assert optics.beam_diameter(90, 5) == pytest.approx(10.0)
    assert optics.illuminance_lux(1000, 10) == pytest.approx(10.0)
    cd = optics.candela_from_lumens(1000, 30)
    assert optics.lumens_from_candela(cd, 30) == pytest.approx(1000)
    r, g, b = optics.cct_to_rgb(3200)
    assert r == 1.0 and b < g < r
    rep = optics.calibration_report({"beam_angle": 20.0, "candela": 1000.0}, 10, measured_diameter=4.0, measured_lux=5.0)
    assert rep["beam_scale"] == pytest.approx(4.0 / optics.beam_diameter(20, 10))
    assert rep["intensity_scale"] == pytest.approx(0.5)   # predicted 10 lx, measured 5 lx


# ---- GDTF / MVR ------------------------------------------------------------------------------------------------
def test_gdtf_import(tmp_path):
    ft = gdtf.import_gdtf(make_gdtf(tmp_path), tmp_path / "out")
    assert (ft.manufacturer, ft.model) == ("ACME", "Test Spot")
    assert ft.optical["beam_angle"] == 12 and ft.optical["lumens"] == 7000 and ft.physical["weight"] == 12.5
    assert ft.geometry["pan_range"] == 540 and ft.geometry["tilt_range"] == 270
    assert ft.geometry["pan_pivot"] == pytest.approx([0, 0, -0.1])
    assert ft.geometry["tilt_pivot"] == pytest.approx([0, 0, -0.4])
    assert ft.geometry["lens_offset"] == pytest.approx([0, 0, -0.2])   # beam at -0.6, relative to the tilt pivot
    mode = ft.mode("Standard")
    assert [c.attribute for c in mode.channels] == ["Pan", "Dimmer", "Gobo1", "Tilt"]
    assert mode.channels[0].offsets == [1, 2] and mode.channels[0].default == 32768
    assert mode.footprint == 5
    star = ft.wheels["Gobo Wheel 1"][1]
    assert Path(star["image"]).read_bytes() == b"PNG"
    data = bytearray(512)
    data[2], data[3] = 255, 20
    st = evaluate(ft, mode, bytes(data))
    assert st.gobo_slot == 1 and st.gobo_image == star["image"]


def test_mvr_import(tmp_path):
    f = tmp_path / "plot.mvr"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("GeneralSceneDescription.xml", MVR_XML)
        z.writestr("truss.glb", b"glb")
    scene = mvr.import_mvr(f, tmp_path / "x")
    fx = scene.fixtures[0]
    assert fx.group == "Front" and fx.layer == "Rig" and fx.fixture_id == "101"
    assert fx.position == pytest.approx((1.5, 2.0, 5.0))
    assert (fx.universe, fx.address) == (3, 1)           # absolute 1025 -> universe 3, channel 1
    assert fx.rotation[0] == (0.0, 1.0, 0.0)
    truss = [i for i in scene.items if i.kind == "truss"][0]
    assert truss.geometry_files == ["truss.glb"] and truss.position[2] == pytest.approx(6.0)
    assert (tmp_path / "x" / "truss.glb").exists()


def test_mvr_export_roundtrip(tmp_path):
    src = tmp_path / "plot.mvr"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("GeneralSceneDescription.xml", MVR_XML)
        z.writestr("truss.glb", b"glb")
    scene = mvr.import_mvr(src, tmp_path / "x")
    out = tmp_path / "out.mvr"
    mvr.export_mvr(out, scene.items, {"t.gdtf": make_gdtf(tmp_path), "truss.glb": b"glb"})
    with zipfile.ZipFile(out) as z:
        assert {"GeneralSceneDescription.xml", "t.gdtf", "truss.glb"} <= set(z.namelist())
    back = mvr.import_mvr(out, tmp_path / "y")
    fx = back.fixtures[0]
    assert (fx.name, fx.group, fx.layer, fx.fixture_id, fx.uuid) == ("Spot 1", "Front", "Rig", "101", "F1")
    assert (fx.universe, fx.address, fx.gdtf_spec, fx.gdtf_mode) == (3, 1, "t.gdtf", "Standard")
    assert fx.position == pytest.approx((1.5, 2.0, 5.0))
    assert [c for row in fx.rotation for c in row] == pytest.approx([c for row in scene.fixtures[0].rotation for c in row])
    truss = [i for i in back.items if i.kind == "truss"][0]
    assert truss.geometry_files == ["truss.glb"] and truss.position[2] == pytest.approx(6.0)


def test_gdtf_export_roundtrip(tmp_path):
    ft = gdtf.import_gdtf(make_gdtf(tmp_path), tmp_path / "out")
    assert Path(ft.geometry["gdtf_file"]).is_file()
    out = tmp_path / "re.gdtf"
    out.write_bytes(gdtf.export_gdtf(ft))
    back = gdtf.import_gdtf(out)
    assert (back.manufacturer, back.model) == ("ACME", "Test_Spot")
    assert back.optical["beam_angle"] == 12 and back.geometry["pan_range"] == 540
    mode = back.mode("Standard")
    assert [c.attribute for c in mode.channels] == ["Pan", "Dimmer", "Gobo1", "Tilt"]
    assert mode.channels[0].offsets == [1, 2] and mode.footprint == 5
    assert back.geometry["pan_pivot"] == pytest.approx([0, 0, -0.1])
    assert back.geometry["lens_offset"] == pytest.approx([0, 0, -0.2])


def test_mvr_address_forms():
    assert mvr.parse_address("2.15") == (2, 15)
    assert mvr.parse_address("512") == (1, 512)
    assert mvr.parse_address("513") == (2, 1)


# ---- QLC+ -----------------------------------------------------------------------------------------------------
def test_qxf_import_channels_and_physical(tmp_path):
    ft = qlc.import_qxf(make_qxf(tmp_path))
    assert (ft.manufacturer, ft.model, ft.kind) == ("Acme", "Spot 2000", "MOVING_HEAD")
    mode = ft.mode("Full")
    assert [c.attribute for c in mode.channels] == ["Shutter", "Dimmer", "Red", "Gobo1", "Gobo1Rot", "Frost", "Iris",
                                                    "Zoom", "Pan", "Tilt"]
    assert mode.channel("Pan").offsets == [9, 10] and mode.channel("Tilt").offsets == [11, 12]
    assert mode.channel("Pan").default == 32768 and mode.channel("Shutter").default == 20   # shutter starts open
    assert mode.footprint == 12
    assert ft.optical["zoom_min"] == 10 and ft.optical["zoom_max"] == 28 and ft.optical["source"] == "Discharge"
    assert ft.optical["lumens"] == 1400 * 12 and ft.provenance["optical"] == "estimated"
    assert ft.physical["width"] == pytest.approx(0.4) and ft.geometry["tilt_range"] == 270
    assert FixtureType.from_json(ft.to_json()).to_dict() == ft.to_dict()


def qxf_state(tmp_path, values: dict):
    ft = qlc.import_qxf(make_qxf(tmp_path))
    mode = ft.mode("Full")
    data = bytearray(512)
    for attr, v in values.items():
        data[mode.channel(attr).offsets[0] - 1] = v
    return evaluate(ft, mode, bytes(data)), ft


def test_qxf_shutter_strobe_direction(tmp_path):
    assert qxf_state(tmp_path, {"Shutter": 5})[0].shutter_closed
    assert not qxf_state(tmp_path, {"Shutter": 30})[0].shutter_closed
    slow, fast = qxf_state(tmp_path, {"Shutter": 50})[0], qxf_state(tmp_path, {"Shutter": 99})[0]
    assert 0 < slow.strobe_hz < fast.strobe_hz
    assert qxf_state(tmp_path, {"Shutter": 100})[0].strobe_hz > qxf_state(tmp_path, {"Shutter": 255})[0].strobe_hz


def test_qxf_iris_frost_zoom_use_function_ranges(tmp_path):
    base, _ = qxf_state(tmp_path, {})
    assert base.iris == 1.0 and base.frost == 0.0 and base.beam_angle == pytest.approx(28.0)   # zoom 0 = widest
    assert qxf_state(tmp_path, {"Iris": 199})[0].iris == pytest.approx(0.1)
    assert qxf_state(tmp_path, {"Iris": 210})[0].iris == pytest.approx(0.1)        # "Closed" range
    assert qxf_state(tmp_path, {"Iris": 100})[0].iris == pytest.approx(1 - 0.9 * 100 / 199)
    assert qxf_state(tmp_path, {"Frost": 88})[0].frost == pytest.approx(87 / 174)
    assert qxf_state(tmp_path, {"Frost": 200})[0].frost == 0.0                      # macro range, not frost
    assert qxf_state(tmp_path, {"Zoom": 255})[0].beam_angle == pytest.approx(10.0)


def test_qxf_gobo_slots_images_and_rotation(tmp_path):
    st, ft = qxf_state(tmp_path, {"Gobo1": 12})
    assert st.gobo_slot == 1 and st.gobo_image.endswith("Acme/star.png")
    st, _ = qxf_state(tmp_path, {"Gobo1": 25})
    assert st.gobo_slot == 2 and st.gobo_image == ""                                  # SVG gobos are not loadable
    assert qxf_state(tmp_path, {"Gobo1": 5})[0].gobo_slot == 0
    assert qxf_state(tmp_path, {"Gobo1Rot": 1})[0].gobo_rot_dps == 0
    cw_slow, cw_fast = qxf_state(tmp_path, {"Gobo1Rot": 10})[0], qxf_state(tmp_path, {"Gobo1Rot": 120})[0]
    assert 0 < cw_slow.gobo_rot_dps < cw_fast.gobo_rot_dps
    ccw_fast, ccw_slow = qxf_state(tmp_path, {"Gobo1Rot": 130})[0], qxf_state(tmp_path, {"Gobo1Rot": 250})[0]
    assert ccw_fast.gobo_rot_dps < ccw_slow.gobo_rot_dps < 0


def test_qxf_search(tmp_path):
    f = make_qxf(tmp_path)
    dirs = [f.parents[1]]
    assert qlc.search_fixtures("acme spot", dirs) == [f]
    assert qlc.search_fixtures("Spot-2000", dirs) == [f]
    assert qlc.search_fixtures("martin", dirs) == []


REAL_MAC = Path("/usr/share/qlcplus/fixtures/Martin/Martin-MAC-2000-Profile-II.qxf")


@pytest.mark.skipif(not REAL_MAC.exists(), reason="QLC+ fixture library not installed")
def test_real_qlc_library_imports():
    ft = qlc.import_qxf(REAL_MAC)
    mode = ft.mode("24 Channel - 16 Bit")
    assert mode.footprint == 24 and mode.channel("Pan").offsets == [19, 20]
    assert ft.geometry["pan_range"] == 540
    base = REAL_MAC.parents[1]
    for p in sorted(base.glob("*/*.qxf"))[:300]:       # every definition must at least import and evaluate
        t = qlc.import_qxf(p)
        for m in t.modes:
            evaluate(t, m, bytes(512))


def test_multicast_membership_follows_requested_universes():
    rx = NetworkReceiver(DmxEngine())
    rx.start(ReceiverConfig(protocol="sacn", sacn_port=0, multicast=True))
    try:
        rx.update_multicast([1, 2])
        assert set(rx.joined) | rx._failed == {"239.255.0.1", "239.255.0.2"}   # sandboxes may refuse the join
        rx.update_multicast([2])
        assert "239.255.0.1" not in rx.joined
        rx.update_multicast([])
        assert rx.joined == []
    finally:
        rx.stop()


# ---- generic moving-head definitions behave like a typical DMX chart -----------------------------------------------
def generic_state(fid, **channels):
    ft = LIB.get(fid)
    mode = ft.modes[0]
    data = bytearray(512)
    for attr, v in channels.items():
        data[mode.channel(attr).offsets[0] - 1] = v
    return evaluate(ft, mode, bytes(data))


def test_generic_color_wheel_slots_and_rainbow():
    assert generic_state("generic.mh_beam", ColorWheel=3).rgb == (1.0, 1.0, 1.0)          # open
    assert generic_state("generic.mh_beam", ColorWheel=10).rgb == (1.0, 0.0, 0.0)         # red
    blue = generic_state("generic.mh_beam", ColorWheel=50).rgb
    assert blue[2] > blue[0] and blue[2] > blue[1]
    slow, fast = generic_state("generic.mh_beam", ColorWheel=130), generic_state("generic.mh_beam", ColorWheel=190)
    assert 0 < slow.color_cycle_hz < fast.color_cycle_hz and slow.animated
    assert generic_state("generic.mh_beam", ColorWheel=200).color_cycle_hz < 0
    assert slow.color_at(0.0) != slow.color_at(0.4)                                      # hue moves with time


def test_generic_gobo_prism_shutter_ranges():
    assert generic_state("generic.mh_beam", Gobo1=3).gobo_slot == 0
    assert generic_state("generic.mh_beam", Gobo1=20).gobo_slot == 2
    assert generic_state("generic.mh_beam", Gobo1=70).gobo_slot == 1                       # shake range of gobo 1
    assert not generic_state("generic.mh_beam", Prism1=100).prism
    assert generic_state("generic.mh_beam", Prism1=200).prism
    assert generic_state("generic.mh_beam", Shutter=4).shutter_closed
    assert not generic_state("generic.mh_beam", Shutter=10).shutter_closed
    assert generic_state("generic.mh_beam", Shutter=100).strobe_hz > 0
    assert generic_state("generic.mh_beam", Shutter=200).strobe_hz == 0
    assert LIB.get("generic.mh_beam").modes[0].channel("Shutter").default == 8             # starts open


def test_qlc_wheel_colours_from_names():
    cap = {"preset": "", "name": "Light Blue", "res1": "", "res2": ""}
    assert qlc._wheel_color(cap)[2] > 0.9
    assert qlc._wheel_color({**cap, "name": "Position 1 > Position 2"}) is None
    mix = qlc._wheel_color({"preset": "ColorDoubleMacro", "name": "x", "res1": "#ff0000", "res2": "#0000ff"})
    assert mix[0] == pytest.approx(0.5) and mix[2] == pytest.approx(0.5)


# ---- bundled GDTF Share library --------------------------------------------------------------------------------
def test_bundled_gdtf_fixtures_are_listed_and_load_lazily(tmp_path):
    lib = Library(cache_dir=tmp_path)
    share = [t for t in lib.all() if t.id.startswith("gdtf.share.")]
    assert len(share) >= 70 and all(not t.modes for t in share)           # stubs only until first use
    pointe = next(t for t in share if t.model == "Robin Pointe")
    assert pointe.category == "Moving Heads" and pointe.provenance["dmx"] == "manufacturer"
    loaded = lib.get(pointe.id)
    assert loaded.modes and loaded.id == pointe.id and loaded.has_attribute("Pan")
    assert {p["role"] for p in loaded.geometry["parts"]} == {"base", "yoke", "head"}
    assert all(Path(p["file"]).is_file() for p in loaded.geometry["parts"])
    assert loaded.geometry["tilt_pivot"][2] == pytest.approx(-0.3547)


def test_every_bundled_gdtf_file_imports():
    lib = Library()
    for t in lib.all():
        if t.id.startswith("gdtf.share."):
            loaded = lib.get(t.id)
            assert loaded.modes, t.label
            for m in loaded.modes[:1]:
                evaluate(loaded, m, bytes(512))


def test_gdtf_matrix_formats():
    rot, pos = gdtf._parse_position("{1,0,0,0}{0,1,0,0}{0,0,1,-0.09}{0,0,0,1}")        # GDTF 1.2: origin in last column
    assert pos == (0.0, 0.0, -0.09)
    rot, pos = gdtf._parse_position("{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,-0.1,1}")         # older: origin in last row
    assert pos == (0.0, 0.0, -0.1)


def test_3ds_reader_and_unit_scale():
    import struct
    from lighting_visualizer.core import model3ds

    def chunk(cid, body):
        return struct.pack("<HI", cid, 6 + len(body)) + body
    verts = b"".join(struct.pack("<3f", *v) for v in ((0, 0, 0), (100, 0, 0), (0, 100, 0)))
    mesh = chunk(0x4110, struct.pack("<H", 3) + verts) + chunk(0x4120, struct.pack("<H", 1) + struct.pack("<4H", 0, 1, 2, 0))
    data = chunk(0x4D4D, chunk(0x3D3D, chunk(0x4000, b"obj\x00" + chunk(0x4100, mesh))))
    meshes = model3ds.read_3ds(data)
    assert meshes[0][0] == "obj" and len(meshes[0][1]) == 3 and meshes[0][2] == [(0, 1, 2)]
    assert model3ds.unit_scale(meshes, [0.1, 0.1, 0.05]) == 0.001


def test_framing_blades_follow_dmx():
    lib = Library()
    t = lib.get(next(x.id for x in lib.all() if x.model == "Robin T1 Profile FS"))
    mode = t.modes[0]
    data = bytearray(512)
    assert not evaluate(t, mode, bytes(data)).framed
    data[mode.channel("Blade1A").offsets[0] - 1] = 255
    data[mode.channel("Blade2Rot").offsets[0] - 1] = 255
    st = evaluate(t, mode, bytes(data))
    assert st.framed and st.blades[0][0] == 1.0 and st.blades[1][2] != 0
