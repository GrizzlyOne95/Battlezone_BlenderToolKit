"""Legacy .sdf (structure) + .geo <-> neutral scene. Maya-free.

Shares the part / GEO / hierarchy handling with ``vdf_io``; this module only
adds the SDF container: ``sdf_classes`` sections SDFC, SGEO and the GEO
records (which carry five extra fields: ddr, x, y, z, time).

Layout (checked against ``export_sdf.py`` and stock files): SGEO holds
``geocount`` slots in each of 6 bands; LOD1 / LOD2 / LOD3 live in bands 0 / 2 / 4
and bands 1 / 3 / 5 are NULL. Stock files often repeat the LOD1 record names in
the LOD2 / LOD3 bands (the same .geo is reused); those repeats are folded into
the single LOD1 node on read, and written as NULL unless a distinct LOD2 / LOD3
part exists. SDF part names are not always LOD-digit style (``op1bda01``); such
names are treated as LOD1 (see ``vdf_io.make_layout``).

Kept as scene meta (model header attributes): structure name and type, LOD distances,
the SDFC ``ddr`` value, death explosion effect and sound. Kept per part (authoring
attributes): ddr, x, y, z, time, plus the VDF part fields. Not round-tripped: ANIM block and
anything after it, damage-state data, and the same per-face fields listed in
``vdf_io``.
"""

from __future__ import annotations

import os
from typing import List

from . import vdf_io
from .model import Scene
from .toolkit import ensure_toolkit

SDF_LOD_BANDS = (0, 2, 4)
SDF_BAND_COUNT = 6
SDF_RECORD_SIZE = 120
NULL_SLOT_DDR = 50000  # what export_sdf.py gives NULL slots
DEFAULT_PART_DDR = 0  # stock parts store 0


def _modules():
    ensure_toolkit()
    from bz98tools import sdf_classes, vdf_classes

    return sdf_classes, vdf_classes


def read_sdf(path: str, import_options=None) -> Scene:
    sdf_classes, _ = _modules()
    with open(path, "rb") as handle:
        data = handle.read()

    header = sdf_classes.SDFHeader()
    position = header.Read(data, 0)
    if header.BWDHeader != b"BWD2" or header.REVHeader != b"REV\0":
        raise ValueError("%s is not an SDF file or is corrupted" % os.path.basename(path))
    sdfc = sdf_classes.SDFCHeader()
    position = sdfc.Read(data, position)
    sgeo = sdf_classes.SGEOHeader()
    position = sgeo.Read(data, position)
    if sdfc.headername != "SDFC" or sgeo.headername != "SGEO":
        raise ValueError("%s: missing SDFC / SGEO sections" % os.path.basename(path))
    if sgeo.geocount < 0 or position + SDF_BAND_COUNT * sgeo.geocount * SDF_RECORD_SIZE > len(data):
        raise ValueError("%s: truncated GEO table" % os.path.basename(path))

    scene = Scene()
    scene.meta = {
        "format": "sdf",
        "name": sdfc.name,
        "structuretype": int(sdfc.structuretype),
        "lod_dists": [float(v) for v in (sdfc.lod1dist, sdfc.lod2dist, sdfc.lod3dist, sdfc.lod4dist, sdfc.lod5dist)],
        "ddr": int(sdfc.ddr),
        "explosioneffect": sdfc.explosioneffect,
        "explosionsound": sdfc.explosionsound,
    }

    records = []  # (lod, GEOData, owner)
    owners = {}
    ignored = 0
    for band in range(SDF_BAND_COUNT):
        for slot in range(sgeo.geocount):
            geo = sdf_classes.GEOData()
            position = geo.Read(data, position)
            if not geo.name or geo.name[0:4].lower() == "null":
                continue
            if band in SDF_LOD_BANDS:
                lod = SDF_LOD_BANDS.index(band) + 1
                if lod == 1:
                    owners[slot] = geo.name.lower()
                records.append((lod, geo, owners.get(slot)))
            else:
                ignored += 1
    if ignored:
        scene.warnings.append(f"{ignored} record(s) in the unused SGEO bands 1/3/5 were ignored")

    vdf_io.build_nodes(
        records,
        os.path.dirname(os.path.abspath(path)),
        scene,
        extra_props=lambda geo: {
            "ddr": int(geo.ddr),
            "x": float(geo.x),
            "y": float(geo.y),
            "z": float(geo.z),
            "time": float(geo.time),
        },
        quiet_duplicates=True,
    )
    vdf_io.attach_map_textures(scene, os.path.dirname(os.path.abspath(path)), import_options)
    return scene


def write_sdf(scene: Scene, path: str, write_geos: bool = True, face_plane_mode: str = "STOCK") -> List[str]:
    """Write ``path`` and a ``<name>.geo`` beside it for every part with a mesh."""
    sdf_classes, vdf_classes = _modules()
    folder = os.path.dirname(os.path.abspath(path))
    warnings: List[str] = list(scene.warnings)
    layout = vdf_io.make_layout(scene, warnings)
    geocount = len(layout.placed)
    from . import authoring

    meta = dict(authoring.default_meta())
    meta.update(scene.meta or {})

    sdfc = sdf_classes.SDFCHeader()
    sdfc.name = str(meta.get("name") or os.path.splitext(os.path.basename(path))[0])[:16]
    sdfc.structuretype = int(meta["structuretype"])
    (sdfc.lod1dist, sdfc.lod2dist, sdfc.lod3dist, sdfc.lod4dist, sdfc.lod5dist) = vdf_io._lod_dists(meta)
    sdfc.ddr = int(meta["ddr"])
    sdfc.explosioneffect = str(meta["explosioneffect"] or "")[:12]
    sdfc.explosionsound = str(meta["explosionsound"] or "")[:12]

    null_geo = sdf_classes.GEOData()
    null_geo.name = "NULL"
    null_geo.matrix = [0] * 12
    null_geo.parent = "NULL"
    null_geo.geocenter = [0] * 3
    null_geo.sphereradius = 0
    null_geo.boxhalfheight = [0] * 3
    null_geo.type = 0
    null_geo.geoflags = 0
    null_geo.ddr = NULL_SLOT_DDR
    null_geo.x = null_geo.y = null_geo.z = null_geo.time = 0.0

    def extras(record, props):
        record.ddr = int(props.get("ddr", DEFAULT_PART_DDR))
        record.x = float(props.get("x", 0.0))
        record.y = float(props.get("y", 0.0))
        record.z = float(props.get("z", 0.0))
        record.time = float(props.get("time", 0.0))

    records, _points = vdf_io.fill_records(
        layout, folder, warnings, sdf_classes.GEOData, vdf_classes.serialize_section,
        vdf_classes.serialize_section(null_geo), SDF_LOD_BANDS, SDF_BAND_COUNT, extras=extras,
        write_geos=write_geos, face_plane_mode=face_plane_mode,
    )

    sgeo = sdf_classes.SGEOHeader()
    sgeo.geocount = geocount
    sgeo.sectionlength = geocount * SDF_RECORD_SIZE * SDF_BAND_COUNT + sgeo.binlength

    with open(path, "wb") as handle:
        sdf_classes.SDFHeader().Write(handle, 0)
        sdfc.Write(handle, 0)
        sgeo.Write(handle, 0)
        for record in records:
            handle.write(record)
        exit_section = sdf_classes.EXITSection()
        exit_section.Write(handle, 0)
        exit_section.Write(handle, 0)
    return warnings
