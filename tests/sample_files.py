"""Sample GDTF / MVR content shared by the pytest suite and the Blender smoke test."""
import zipfile

GDTF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<GDTF DataVersion="1.1"><FixtureType Name="Test Spot" Manufacturer="ACME" Description="d">
<Wheels><Wheel Name="Gobo Wheel 1"><Slot Name="Open"/><Slot Name="Star" MediaFileName="star"/></Wheel>
<Wheel Name="Color Wheel 1"><Slot Name="Red" Color="0.64,0.33,0.2"/></Wheel></Wheels>
<PhysicalDescriptions><Properties><Weight Value="12.5"/></Properties></PhysicalDescriptions>
<Geometries><Geometry Name="Body" Position="{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,0,1}">
<Axis Name="Yoke" Position="{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,-0.1,1}">
<Axis Name="Head" Position="{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,-0.3,1}">
<Beam Name="Beam" Position="{1,0,0,0}{0,1,0,0}{0,0,1,0}{0,0,-0.2,1}" BeamAngle="12" FieldAngle="20" LuminousFlux="7000" ColorTemperature="6000" LampType="Discharge"/>
</Axis></Axis></Geometry></Geometries>
<DMXModes><DMXMode Name="Standard"><DMXChannels>
<DMXChannel Offset="1,2" Geometry="Yoke" Default="32768/2"><LogicalChannel Attribute="Pan"><ChannelFunction Name="Pan" Attribute="Pan" DMXFrom="0/2" PhysicalFrom="-270" PhysicalTo="270"/></LogicalChannel></DMXChannel>
<DMXChannel Offset="3" Default="0/1"><LogicalChannel Attribute="Dimmer"><ChannelFunction Name="Dimmer" DMXFrom="0/1"/></LogicalChannel></DMXChannel>
<DMXChannel Offset="4" Default="0/1"><LogicalChannel Attribute="Gobo1"><ChannelFunction Name="Gobos" Wheel="Gobo Wheel 1" DMXFrom="0/1">
<ChannelSet Name="Open" DMXFrom="0/1" WheelSlotIndex="1"/><ChannelSet Name="Star" DMXFrom="10/1" WheelSlotIndex="2"/></ChannelFunction></LogicalChannel></DMXChannel>
<DMXChannel Offset="5" Geometry="Head" Default="128/1"><LogicalChannel Attribute="Tilt"><ChannelFunction Name="Tilt" DMXFrom="0/1" PhysicalFrom="-135" PhysicalTo="135"/></LogicalChannel></DMXChannel>
<DMXChannel Offset="None"><LogicalChannel Attribute="Virtual"/></DMXChannel>
</DMXChannels></DMXMode></DMXModes></FixtureType></GDTF>"""

MVR_XML = """<GeneralSceneDescription verMajor="1" verMinor="6"><Scene><Layers>
<Layer name="Rig" uuid="L1"><ChildList>
<GroupObject name="Front"><Matrix>{1,0,0}{0,1,0}{0,0,1}{1000,0,5000}</Matrix><ChildList>
<Fixture name="Spot 1" uuid="F1"><Matrix>{0,1,0}{-1,0,0}{0,0,1}{500,2000,0}</Matrix>
<GDTFSpec>t.gdtf</GDTFSpec><GDTFMode>Standard</GDTFMode><FixtureID>101</FixtureID>
<Addresses><Address break="0">1025</Address></Addresses></Fixture></ChildList></GroupObject>
<Truss name="T1"><Matrix>{1,0,0}{0,1,0}{0,0,1}{0,0,6000}</Matrix><Geometries><Geometry3D fileName="truss.glb"/></Geometries></Truss>
</ChildList></Layer></Layers></Scene></GeneralSceneDescription>"""


def make_gdtf(tmp_path):
    f = tmp_path / "t.gdtf"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("description.xml", GDTF_XML)
        z.writestr("wheels/star.png", b"PNG")
    return f


QXF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<FixtureDefinition xmlns="http://www.qlcplus.org/FixtureDefinition">
 <Manufacturer>Acme</Manufacturer><Model>Spot 2000</Model><Type>Moving Head</Type>
 <Channel Name="Shutter"><Group Byte="0">Shutter</Group>
  <Capability Min="0" Max="19" Preset="ShutterClose">Shutter closed</Capability>
  <Capability Min="20" Max="49" Preset="ShutterOpen">Shutter open</Capability>
  <Capability Min="50" Max="99" Preset="StrobeSlowToFast">Strobe (slow &gt; fast)</Capability>
  <Capability Min="100" Max="255" Preset="StrobeFastToSlow">Strobe (fast &gt; slow)</Capability></Channel>
 <Channel Name="Dimmer" Preset="IntensityDimmer"/>
 <Channel Name="Red" Preset="IntensityRed"/>
 <Channel Name="Gobo"><Group Byte="0">Gobo</Group>
  <Capability Min="0" Max="9" Preset="GoboMacro" Res1="Others/open.svg">Open</Capability>
  <Capability Min="10" Max="19" Preset="GoboMacro" Res1="Acme/star.png">Star</Capability>
  <Capability Min="20" Max="29" Preset="GoboMacro" Res1="Acme/dots.svg">Dots</Capability></Channel>
 <Channel Name="Gobo Rotation"><Group Byte="0">Gobo</Group>
  <Capability Min="0" Max="2" Preset="RotationStop">No rotation</Capability>
  <Capability Min="3" Max="127" Preset="RotationClockwiseSlowToFast">CW</Capability>
  <Capability Min="128" Max="255" Preset="RotationCounterClockwiseFastToSlow">CCW</Capability></Channel>
 <Channel Name="Effects"><Group Byte="0">Effect</Group>
  <Capability Min="0" Max="0">Open</Capability>
  <Capability Min="1" Max="175">Variable frost, 0 &lt; 100%</Capability>
  <Capability Min="176" Max="255">Macro</Capability></Channel>
 <Channel Name="Iris"><Group Byte="0">Shutter</Group>
  <Capability Min="0" Max="199" Preset="ShutterOpen">Open &gt; Closed</Capability>
  <Capability Min="200" Max="255" Preset="ShutterClose">Closed</Capability></Channel>
 <Channel Name="Zoom" Preset="BeamZoomBigSmall"/>
 <Channel Name="Pan" Preset="PositionPan"/><Channel Name="Pan Fine" Preset="PositionPanFine"/>
 <Channel Name="Tilt" Preset="PositionTilt"/><Channel Name="Tilt Fine" Preset="PositionTiltFine"/>
 <Mode Name="Full">
  <Physical><Bulb Type="HMI 1200W" Lumens="0" ColourTemperature="6000"/>
   <Dimensions Weight="40" Width="400" Height="500" Depth="700"/><Lens Name="Other" DegreesMin="10" DegreesMax="28"/>
   <Focus Type="Head" PanMax="540" TiltMax="270"/><Technical PowerConsumption="1400"/></Physical>
  <Channel Number="0">Shutter</Channel><Channel Number="1">Dimmer</Channel><Channel Number="2">Red</Channel>
  <Channel Number="3">Gobo</Channel><Channel Number="4">Gobo Rotation</Channel><Channel Number="5">Effects</Channel>
  <Channel Number="6">Iris</Channel><Channel Number="7">Zoom</Channel>
  <Channel Number="8">Pan</Channel><Channel Number="9">Pan Fine</Channel>
  <Channel Number="10">Tilt</Channel><Channel Number="11">Tilt Fine</Channel></Mode>
</FixtureDefinition>"""


def make_qxf(tmp_path):
    root = tmp_path / "qlcplus"
    (root / "fixtures" / "Acme").mkdir(parents=True, exist_ok=True)
    (root / "gobos" / "Acme").mkdir(parents=True, exist_ok=True)
    (root / "gobos" / "Acme" / "star.png").write_bytes(b"PNG")
    f = root / "fixtures" / "Acme" / "Acme-Spot-2000.qxf"
    f.write_text(QXF_XML, encoding="utf-8")
    return f
