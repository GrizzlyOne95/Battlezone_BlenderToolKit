# MakeOBJ Compatibility Export Options

Battlezone's legacy **MakeObj 1.1.5** compiler performed two authoring-time operations that are not inherent to the GEO file format and therefore are not reproduced automatically by a normal Blender export. The toolkit exposes both as optional compatibility switches.

The switches are available in **3D View → Sidebar → Battlezone → MakeOBJ Compatibility** and are **off by default**. They affect direct GEO export and the GEO files generated as part of VDF/SDF export.

## Legacy Vertex Normals

MakeOBJ rebuilds every vertex normal rather than copying the source model's vertex normals. For each vertex it:

1. Finds every polygon referencing that vertex.
2. Normalizes each polygon normal.
3. Adds those unit normals with equal weight.
4. Normalizes the resulting sum.

Face area is not used as a weight. This can differ from Blender's normal calculation, especially on meshes where adjacent faces have very different areas or custom/split normals are present.

Enable **Legacy Vertex Normals** when reproducing output from the historical MakeOBJ pipeline is more important than preserving Blender-authored normals.

## ComputePolyColor

MakeOBJ's `ComputePolyColor` derives the stored GEO face RGB from the texture covered by the face's UV polygon. Static analysis of MakeObj 1.1.5 establishes the following behavior:

- UV coordinates are scaled by `texture_size - 1` and converted to integers by truncation toward zero.
- Polygon edges are rasterized into inclusive scanline spans.
- Sample coordinates are wrapped with `coord & (size - 1)`, not `% size`. This is exact for the power-of-two textures expected by the legacy Battlezone toolchain.
- RGB texel bytes for every covered sample are summed.
- The stored face RGB is the integer average of those samples.

The Blender implementation uses the selected Image Texture node when one exists, otherwise the first Image Texture node on the face material. If the image cannot be sampled, export falls back to the material diffuse color and prints a warning.

Blender exposes normal sRGB texture pixels in scene-linear form. For images identified as sRGB, the compatibility path converts the sampled values back through the standard sRGB transfer function before reconstructing byte RGB values. Non-color/raw image data is sampled directly. This is intended to reproduce MakeOBJ's stb_image byte input as closely as Blender's color-managed image representation permits.

## Scope and evidence

These options reproduce recovered MakeOBJ **authoring algorithms**; they do not change the GEO binary layout. Existing import/export support for face plane data, face Parent/Node values, VDF/SDF records, animations, and other legacy semantics is unchanged.

The normal algorithm and the core ComputePolyColor coordinate/raster/wrap/average behavior were reconstructed from MakeObj 1.1.5 static analysis. Exact byte-for-byte agreement can still depend on the source image decoder/color pipeline, so comparisons against original MakeOBJ output should use the same source texture files when validating historical assets.
