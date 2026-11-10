"""Deliberately Blender-free: `core` must import without bpy so it can be tested outside Blender."""
__version__ = (0, 2, 0)

bl_info = {
    "name": "Lighting Visualizer",
    "author": "Pötkö",
    "version": __version__,
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Lighting",
    "description": "Fixture library, sACN / Art-Net input (grandMA2 / grandMA3), GDTF / MVR import and lighting visualization",
    "category": "Lighting",
}


def register():
    from . import bl
    bl.register()


def unregister():
    from . import bl
    bl.unregister()
