from __future__ import annotations

"""Selection-safe orchestration for Ogre "Batch Selected" export.

The actual mesh serializer remains the normal single-export backend. Batch mode
only isolates one selected mesh object at a time, derives its destination path,
and restores Blender selection/active-object state afterward.
"""

import os


def export_selected_individually(context, filepath, export_one):
    """Export each selected non-armature object through ``export_one``.

    ``export_one`` receives ``(output_path, object)`` and returns the same
    Blender operator result set as the ordinary exporter. The caller decides
    how to handle a non-FINISHED result.
    """

    layer_objects = list(context.view_layer.objects)
    selected = [
        obj
        for obj in layer_objects
        if obj.select_get() and getattr(obj, "type", None) != "ARMATURE"
    ]
    if not selected:
        return []

    selection_state = [(obj, bool(obj.select_get())) for obj in layer_objects]
    active = getattr(context.view_layer.objects, "active", None)
    directory = os.path.dirname(filepath) or "."
    results = []

    try:
        for obj in selected:
            for layer_obj in layer_objects:
                if layer_obj.select_get():
                    layer_obj.select_set(False)
            obj.select_set(True)
            context.view_layer.objects.active = obj

            output_path = os.path.join(directory, f"{obj.name}.mesh")
            results.append((obj, output_path, export_one(output_path, obj)))
    finally:
        for obj, was_selected in selection_state:
            obj.select_set(was_selected)
        context.view_layer.objects.active = active

    return results


__all__ = ["export_selected_individually"]
