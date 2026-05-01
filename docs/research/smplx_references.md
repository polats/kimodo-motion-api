# SMPL-X references

External projects we're using as **reference** (not vendored / not submoduled) while
building the simple web viewer with shape and expression controls.

## Reference implementations

- **SMPL-X Blender addon** — https://gitlab.tuebingen.mpg.de/jtesch/smplx_blender_addon
  Python (`bpy`) plugin. Useful for how it exposes shape/expression sliders and how
  it recomputes joint rest positions when betas change.

- **SMPL-X Unity package** — https://gitlab.tuebingen.mpg.de/jtesch/smplx-unity
  C# implementation of the same idea in a runtime engine. Useful for cross-checking
  morph-target / blendshape conventions.

- **SMPLify-X** — https://github.com/vchoutas/smplify-x
  *Inverse problem*: fits SMPL-X parameters to an image. **Not directly relevant** to
  authoring shapes/expressions; kept here only for completeness in case we later want
  image-driven body fitting.

## What we extract conceptually

- `v_shaped = v_template + shapedirs @ [betas; expression]` — same matrix multiply
  used by all three references.
- `J = J_regressor @ v_shaped` — rest joint positions follow shape.
- Hand and face PCAs exist as separate parameter sets in SMPL-X but aren't required
  for the body+expression viewer scope.

## Local data

The model file `SMPLX_NEUTRAL.npz` (license-gated, from
https://smpl-x.is.tue.mpg.de/) lives at
`kimodo/assets/skeletons/smplx22/SMPLX_NEUTRAL.npz` — already used by
`kimodo/viz/smplx_skin.py` and now also by `kimodo/scripts/export_smplx_glb.py`.
