import bpy
from bpy.app.handlers import persistent

from . import creator, operators, overlay, props, runtime, ui

_classes = props.classes + operators.classes + creator.classes + ui.classes


@persistent
def _on_load(_):
    runtime.request_rebuild()


def _deferred_init():
    runtime.init_library()
    runtime.refresh_interfaces()
    return None


def register():
    for c in _classes:
        bpy.utils.register_class(c)
    props.register_types()
    creator.register_types()
    bpy.app.handlers.load_post.append(_on_load)
    bpy.app.timers.register(_deferred_init, first_interval=0.1)
    overlay.register()
    runtime.start_timer()


def unregister():
    runtime.stop_timer()
    overlay.unregister()
    if bpy.app.timers.is_registered(_deferred_init):
        bpy.app.timers.unregister(_deferred_init)
    if _on_load in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_on_load)
    creator.unregister_types()
    props.unregister_types()
    for c in reversed(_classes):
        bpy.utils.unregister_class(c)
