"""Substance 3D Painter startup plugin: unattended mesh-map bakes for BZR models.

Idle unless run_bake.ps1 left a job pointer (python/bzr_bake_job.txt, deleted
on read) or the environment variable BZR_BAKE_JOB names a job JSON.  For every mesh in the job it:
  1. creates a project from the OBJ (one texture set per Ogre material),
  2. assigns the material's existing BZR normal map as the Normal mesh map,
  3. bakes only the requested mesh maps (default AO, Curvature, Thickness)
     with the low poly used as its own high poly,
  4. exports each map as <out>/<mesh>/<material>_<MAP>.png plus a packed
     <material>_ACT.png (R=AO, G=curvature, B=thickness),
  5. closes the project without saving and moves on.
Painter quits when the job finishes.  Progress and errors go to
<out>/bake_log.txt and per-mesh results to <out>/bake_results.json.

Install: copy into Documents/Adobe/Adobe Substance 3D Painter/python/startup.
"""

import json
import os
import re
import time
import traceback
from pathlib import Path

import substance_painter.application as application
import substance_painter.baking as baking
import substance_painter.event as event
import substance_painter.export as export
import substance_painter.project as project
import substance_painter.resource as resource
import substance_painter.textureset as textureset
from substance_painter.baking import BakingParameters, CurvatureMethod
from substance_painter.textureset import MeshMapUsage

try:
    from PySide6 import QtCore
except ImportError:
    from PySide2 import QtCore

# MeshMapUsage -> (export srcMapName, file suffix)
MAPS = {
    "AO": (MeshMapUsage.AO, "ambient_occlusion", "AO"),
    "Curvature": (MeshMapUsage.Curvature, "curvature", "CURV"),
    "Thickness": (MeshMapUsage.Thickness, "thickness", "THICK"),
    "Position": (MeshMapUsage.Position, "position", "POS"),
    "WorldSpaceNormal": (MeshMapUsage.WorldSpaceNormal, "world_space_normals", "WSN"),
}
PACK = ("AO", "Curvature", "Thickness")  # R, G, B of the packed map

_job = None


class BakeJob:
    def __init__(self, path: Path):
        self.cfg = json.loads(path.read_text())
        self.out = Path(self.cfg["out"])
        self.out.mkdir(parents=True, exist_ok=True)
        self.entries = json.loads(Path(self.cfg["manifest"]).read_text())
        only = {m.lower() for m in self.cfg.get("only") or []}
        if only:
            self.entries = [e for e in self.entries if e["mesh"].lower() in only]
        self.maps = [m for m in self.cfg.get("maps", ["AO", "Curvature", "Thickness"]) if m in MAPS]
        self.results = []
        self.queue = []
        self.current = None
        self.started = 0.0
        self.dumped_params = False
        self.log_path = self.out / "bake_log.txt"

    # ---------------------------------------------------------- plumbing
    def log(self, msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print("[bzr_bake]", line)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def later(self, fn, ms=250):
        QtCore.QTimer.singleShot(ms, lambda: self._guard(fn))

    def _guard(self, fn):
        try:
            fn()
        except Exception:
            self.fail(traceback.format_exc())

    def fail(self, why):
        self.log(f"FAIL {self.current and self.current['mesh']}: {why}")
        if self.current is not None:
            self.results.append({"mesh": self.current["mesh"], "status": "error", "error": why})
        self.close_and_next()

    # ---------------------------------------------------------- flow
    def start(self):
        for sig, fn in ((event.ProjectEditionEntered, self.on_project_ready),
                        (event.BakingProcessEnded, self.on_bake_ended),
                        (event.ProjectClosed, self.on_project_closed)):
            event.DISPATCHER.connect_strong(sig, fn)
        self.log(f"job: {len(self.entries)} meshes, maps {self.maps}")
        self.next_mesh()

    def next_mesh(self):
        while self.entries:
            entry = self.entries.pop(0)
            dst = self.out / entry["mesh"]
            if self.cfg.get("skip_existing", True) and dst.is_dir() and any(dst.glob("*_AO.png")):
                self.log(f"skip {entry['mesh']} (already baked)")
                continue
            self.current = entry
            self.started = time.time()
            self.open_mesh(entry)
            return
        self.finish()

    def open_mesh(self, entry):
        if project.is_open():
            project.close()
            self.later(lambda: self.open_mesh(entry), 500)
            return
        self.log(f"open {entry['mesh']}")
        settings = project.Settings(
            normal_map_format=project.NormalMapFormat.DirectX,  # BZBase: green = -dh/drow
            tangent_space_mode=project.TangentSpace.PerFragment,
            default_texture_resolution=self.cfg.get("resolution", 1024),
            import_cameras=False)
        project.create(entry["obj"], settings=settings)

    def on_project_ready(self, _evt):
        if self.current is None:
            return
        self.later(self.configure, 500)

    def configure(self):
        entry = self.current
        self.queue = []
        for ts in textureset.all_texture_sets():
            mat = entry["materials"].get(ts.name(), {})
            size = self.bake_size(mat)
            ts.set_resolution(textureset.Resolution(size, size))
            if mat.get("normal"):
                res = resource.import_project_resource(mat["normal"], resource.Usage.TEXTURE)
                ts.set_mesh_map_resource(MeshMapUsage.Normal, res.identifier())
            self.configure_baking(ts, size, bool(mat.get("normal")))
            self.queue.append(ts)
        self.bake_next()

    def bake_size(self, mat):
        forced = self.cfg.get("bake_size")
        if forced:
            return int(forced)
        size = (mat.get("size") or [self.cfg.get("resolution", 1024)])[0]
        size = max(128, min(int(size), self.cfg.get("max_size", 2048)))
        return 1 << (size - 1).bit_length()  # round up to a power of two

    def configure_baking(self, ts, size, has_normal):
        params = BakingParameters.from_texture_set(ts)
        common = params.common()
        if not self.dumped_params:
            self.dump_params(params)
        values = {}
        log2 = size.bit_length() - 1
        if "OutputSize" in common:
            values[common["OutputSize"]] = (log2, log2)
        low_as_high = next((k for k in common if re.search(r"low.*as.*high", k, re.I)), None)
        if low_as_high:
            values[common[low_as_high]] = True
        elif "HipolyMesh" in common:
            values[common["HipolyMesh"]] = QtCore.QUrl.fromLocalFile(self.current["obj"]).toString()
        self.apply(values, common, self.cfg.get("common", {}))
        for name in self.maps:
            self.apply(values, params.baker(MAPS[name][0]), self.cfg.get("bakers", {}).get(name, {}))
        BakingParameters.set(values)
        params.set_enabled_bakers([MAPS[m][0] for m in self.maps])
        params.set_textureset_enabled(True)
        if "Curvature" in self.maps:
            # with a real BZR normal map, curvature picks up its panel lines
            params.set_curvature_method(
                CurvatureMethod.FromNormalMap if has_normal else CurvatureMethod.FromMesh)

    def apply(self, values, props, overrides):
        for key, val in overrides.items():
            prop = props.get(key)
            if prop is None:
                self.log(f"  ! unknown bake parameter {key!r}")
                continue
            if isinstance(val, str) and hasattr(prop, "enum_value"):
                try:
                    val = prop.enum_value(val)
                except Exception:
                    pass
            values[prop] = tuple(val) if isinstance(val, list) else val

    def dump_params(self, params):
        """Write every baking parameter name/value once, for tuning the job."""
        dump = {"common": {k: repr(p.value()) for k, p in params.common().items()}}
        for name, (usage, _, _) in MAPS.items():
            try:
                dump[name] = {k: repr(p.value()) for k, p in params.baker(usage).items()}
            except Exception as exc:
                dump[name] = repr(exc)
        (self.out / "bake_parameters.json").write_text(json.dumps(dump, indent=1))
        self.dumped_params = True

    def bake_next(self):
        if not self.queue:
            self.later(self.export_maps)
            return
        ts = self.queue.pop(0)
        self.log(f"  bake {ts.name()}")
        baking.bake_async(ts)

    def on_bake_ended(self, evt):
        if self.current is None:
            return
        status = str(getattr(evt, "status", ""))
        if "Success" not in status:
            self.fail(f"bake ended with {status}")
            return
        self.later(self.bake_next)

    def export_maps(self):
        entry = self.current
        dst = self.out / entry["mesh"]
        dst.mkdir(parents=True, exist_ok=True)
        params = {"fileFormat": "png", "bitDepth": str(self.cfg.get("bit_depth", 8)),
                  "dithering": False, "paddingAlgorithm": "infinite"}
        maps = []
        for name in self.maps:
            _, src, suffix = MAPS[name]
            chans = (["R", "G", "B"] if name in ("Position", "WorldSpaceNormal") else ["L"])
            maps.append({"fileName": f"$textureSet_{suffix}", "parameters": params,
                         "channels": [{"destChannel": c, "srcChannel": c, "srcMapType": "meshMap",
                                       "srcMapName": src} for c in chans]})
        if all(m in self.maps for m in PACK):
            maps.append({"fileName": "$textureSet_ACT", "parameters": params, "channels": [
                {"destChannel": c, "srcChannel": "L", "srcMapType": "meshMap",
                 "srcMapName": MAPS[m][1]} for c, m in zip("RGB", PACK)]})
        config = {
            "exportPath": str(dst).replace("\\", "/"),
            "exportShaderParams": False,
            "defaultExportPreset": "bzr_meshmaps",
            "exportPresets": [{"name": "bzr_meshmaps", "maps": maps}],
            "exportList": [{"rootPath": ts.name()} for ts in textureset.all_texture_sets()],
        }
        result = export.export_project_textures(config)
        files = [f for fl in result.textures.values() for f in fl]
        self.log(f"  export {result.status}: {len(files)} files in {time.time() - self.started:.0f}s")
        self.results.append({"mesh": entry["mesh"], "status": str(result.status),
                             "files": files, "seconds": round(time.time() - self.started, 1)})
        self.close_and_next()

    def close_and_next(self):
        self.current = None
        (self.out / "bake_results.json").write_text(json.dumps(self.results, indent=1))
        if project.is_open():
            project.close()  # -> on_project_closed -> next_mesh
        else:
            self.later(self.next_mesh)

    def on_project_closed(self, _evt):
        if self.current is None:
            self.later(self.next_mesh, 500)

    def finish(self):
        self.log(f"done: {len(self.results)} meshes")
        (self.out / "bake_results.json").write_text(json.dumps(self.results, indent=1))
        (self.out / "bake_done.flag").write_text("ok")
        if self.cfg.get("quit_when_done", True):
            self.later(application.close, 1000)


def _job_path():
    # Steam relaunches Painter, which drops the environment, so run_bake.ps1
    # also leaves a one-shot pointer file next to the startup folder.
    path = os.environ.get("BZR_BAKE_JOB")
    pointer = Path(__file__).resolve().parent.parent / "bzr_bake_job.txt"
    if pointer.is_file():
        path = path or pointer.read_text(encoding="utf-8-sig").strip()
        pointer.unlink()
    return Path(path) if path and Path(path).is_file() else None


def start_plugin():
    global _job
    path = _job_path()
    if path is None:
        return
    _job = BakeJob(Path(path))
    # services aren't ready while plugins load; give the UI time to settle
    QtCore.QTimer.singleShot(int(_job.cfg.get("start_delay_ms", 8000)), lambda: _job._guard(_job.start))


def close_plugin():
    global _job
    _job = None
