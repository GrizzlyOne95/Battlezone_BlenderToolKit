"""Locate and import the toolkit's bpy-free serializers.

``bz98tools/__init__.py`` executes the Blender add-on and imports ``bpy``, so
the package is never imported normally here. Like ``tests/_bootstrap.py`` we
register an empty package object whose ``__path__`` points at the real
directory; sub-modules and their relative imports then resolve while the
add-on ``__init__`` never runs.

Search order for the toolkit: ``$BZ98TOOLKIT_PATH`` (the repo root, or the
``bz98tools`` folder itself), then the repository this file lives in
(``maya/bz98maya/core`` -> three levels up), then a ``bz98tools`` folder
bundled next to the ``bz98maya`` package.
"""

from __future__ import annotations

import os
import sys
import types

ENV_VAR = "BZ98TOOLKIT_PATH"
_PKG = "bz98tools"
_MARKER = os.path.join("bzrmodelporter", "bzgeo_serializer.py")


def _candidates():
    env = os.environ.get(ENV_VAR)
    if env:
        yield env
        yield os.path.join(env, _PKG)
    here = os.path.dirname(os.path.abspath(__file__))
    maya_dir = os.path.dirname(os.path.dirname(here))  # .../maya
    yield os.path.join(os.path.dirname(maya_dir), _PKG)  # repo checkout
    yield os.path.join(maya_dir, _PKG)  # bundled copy


def find_toolkit_dir():
    for path in _candidates():
        if os.path.isfile(os.path.join(path, _MARKER)):
            return os.path.abspath(path)
    raise ImportError(
        "Could not find the Battlezone toolkit's 'bz98tools' folder. Set the "
        f"{ENV_VAR} environment variable to the toolkit repository root."
    )


def ensure_toolkit(stub_ogrefast=False):
    """Make ``bz98tools.*`` serializer modules importable. Returns the package dir.

    ``stub_ogrefast`` skips ``bz98tools/ogrefast/__init__.py``, which probes for
    (and may load) the bundled native Ogre backend and installs a global
    ``kenshi_blender_tool`` facade. Inside Maya we do not want either side effect;
    the unit tests keep the real package so they behave like the other tests.
    """
    if _PKG in sys.modules:
        package_dir = list(sys.modules[_PKG].__path__)[0]
    else:
        package_dir = find_toolkit_dir()
        pkg = types.ModuleType(_PKG)
        pkg.__path__ = [package_dir]
        pkg.__doc__ = "bpy-free package shell created by bz98maya"
        sys.modules[_PKG] = pkg

    full = _PKG + ".ogrefast"
    if stub_ogrefast and full not in sys.modules:
        sub = types.ModuleType(full)
        sub.__path__ = [os.path.join(package_dir, "ogrefast")]
        sub.__doc__ = "package shell created by bz98maya"
        sys.modules[full] = sub
        sys.modules[_PKG].ogrefast = sub
    return package_dir
