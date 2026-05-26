// Rig configurations + the data the retargeter needs to align any rest pose
// onto kimodo's SMPL-X rest pose.

// SMPL-X rest world joint positions (from J_regressor @ v_template). Used to
// compute kimodo's bone direction + a per-bone twist-reference vector at rest,
// which together define a full local frame at each joint. The retargeter aligns
// each target bone's rest frame onto this kimodo frame, eliminating both
// direction errors AND twist errors from rest-pose mismatches.
export const SMPLX_REST_WORLD = {
  pelvis:         [ 0.0031, -0.3514,  0.0120],
  left_hip:       [ 0.0613, -0.4442, -0.0140],
  right_hip:      [-0.0601, -0.4553, -0.0092],
  spine1:         [ 0.0004, -0.2415, -0.0156],
  left_knee:      [ 0.1160, -0.8229, -0.0234],
  right_knee:    [-0.1044, -0.8177, -0.0260],
  spine2:         [ 0.0098, -0.1097, -0.0215],
  left_ankle:     [ 0.0726, -1.2260, -0.0552],
  right_ankle:   [-0.0889, -1.2284, -0.0462],
  spine3:         [-0.0015, -0.0574,  0.0069],
  left_foot:      [ 0.1198, -1.2840,  0.0630],
  right_foot:    [-0.1277, -1.2868,  0.0728],
  neck:           [-0.0137,  0.1077, -0.0247],
  left_collar:    [ 0.0448,  0.0275, -0.0003],
  right_collar:  [-0.0492,  0.0269, -0.0065],
  head:           [ 0.0111,  0.2682, -0.0040],
  left_shoulder:  [ 0.1641,  0.0852, -0.0158],
  right_shoulder:[-0.1518,  0.0804, -0.0191],
  left_elbow:     [ 0.4182,  0.0131, -0.0582],
  right_elbow:   [-0.4229,  0.0439, -0.0456],
  left_wrist:     [ 0.6702,  0.0363, -0.0607],
  right_wrist:   [-0.6722,  0.0394, -0.0609],
}

// Each kimodo joint's parent (for inheriting alignment to terminals like wrists,
// where there's no child-direction to compute alignment from).
export const KIMODO_PARENT = {
  pelvis: null,
  left_hip: 'pelvis',
  right_hip: 'pelvis',
  spine1: 'pelvis',
  left_knee: 'left_hip',
  right_knee: 'right_hip',
  spine2: 'spine1',
  left_ankle: 'left_knee',
  right_ankle: 'right_knee',
  spine3: 'spine2',
  left_foot: 'left_ankle',
  right_foot: 'right_ankle',
  neck: 'spine3',
  left_collar: 'spine3',
  right_collar: 'spine3',
  head: 'neck',
  left_shoulder: 'left_collar',
  right_shoulder: 'right_collar',
  left_elbow: 'left_shoulder',
  right_elbow: 'right_shoulder',
  left_wrist: 'left_elbow',
  right_wrist: 'right_elbow',
}

// Primary axis: bone → KIMODO_CHILD[bone]. null = terminal.
export const KIMODO_CHILD = {
  pelvis: 'spine1',
  left_hip: 'left_knee',
  right_hip: 'right_knee',
  spine1: 'spine2',
  left_knee: 'left_ankle',
  right_knee: 'right_ankle',
  spine2: 'spine3',
  left_ankle: 'left_foot',
  right_ankle: 'right_foot',
  spine3: 'neck',
  left_foot: null,
  right_foot: null,
  neck: 'head',
  left_collar: 'left_shoulder',
  right_collar: 'right_shoulder',
  head: null,
  left_shoulder: 'left_elbow',
  right_shoulder: 'right_elbow',
  left_elbow: 'left_wrist',
  right_elbow: 'right_wrist',
  left_wrist: null,
  right_wrist: null,
}

// Twist reference: a second kimodo joint used (alongside the primary axis from
// KIMODO_CHILD) to fully determine the bone's local frame.
//
// Two requirements on each reference choice:
//   1. The reference vector (joint → ref) must not be parallel to the primary
//      axis (otherwise the cross product is degenerate). E.g., legs point down
//      AND the spine points up — using spine1 as a leg reference is degenerate.
//   2. The reference joint must actually be MAPPED in the target rig
//      (otherwise we fall back to direction-only alignment). E.g., the Blender
//      Studio bundle has no spine3, so referencing spine3 from arms means
//      twist isn't constrained.
//
// Below: arms reference spine2 (mapped to chest in Blender Studio); legs
// reference the same-side collar (lateral-and-up from a leg joint, never
// parallel to the leg axis).
export const KIMODO_TWIST_REF = {
  pelvis: 'left_hip',           // primary up (→ spine1), ref lateral (→ hip)
  spine1: 'left_collar',
  spine2: 'left_collar',
  spine3: 'left_collar',
  neck:   'left_collar',
  left_collar:    'spine2',     // primary lateral (→ shoulder), ref up (→ chest)
  right_collar:   'spine2',
  left_shoulder:  'spine2',     // primary along arm, ref up the body
  right_shoulder: 'spine2',
  left_elbow:     'spine2',
  right_elbow:    'spine2',
  left_hip:   'left_collar',    // primary down leg, ref up-and-lateral (avoid parallel)
  right_hip:  'right_collar',
  left_knee:  'left_collar',
  right_knee: 'right_collar',
  left_ankle: 'left_collar',
  right_ankle:'right_collar',
}

// ---- Character configs --------------------------------------------------

export const CHARACTERS = [
  {
    id: 'smplx',
    label: 'SMPL-X (neutral)',
    url: '/models/smplx_neutral.glb',
    skinned: true,
    mapping: identityMapping(),
    scale: 1.0,
  },
  // Skinned variants — built via Blender script: armature → join meshes →
  // auto-weight at down-pose → pose arms to SMPL-X rest direction → apply pose
  // as rest → re-bind weights → export. Bones are named with the kimodo
  // SMPLXSkeleton22 convention so identity mapping works.
  {
    id: 'female_realistic_skinned',
    label: 'Female Realistic (skinned)',
    url: '/models/female_realistic_skinned.glb',
    skinned: true,
    mapping: identityMapping(),
    scale: 1.0,
  },
  {
    id: 'male_realistic_skinned',
    label: 'Male Realistic (skinned)',
    url: '/models/male_realistic_skinned.glb',
    skinned: true,
    mapping: identityMapping(),
    scale: 1.0,
  },
  {
    id: 'female_stylized_skinned',
    label: 'Female Stylized (skinned)',
    url: '/models/female_stylized_skinned.glb',
    skinned: true,
    mapping: identityMapping(),
    scale: 1.0,
  },
  {
    id: 'male_stylized_skinned',
    label: 'Male Stylized (skinned)',
    url: '/models/male_stylized_skinned.glb',
    skinned: true,
    mapping: identityMapping(),
    scale: 1.0,
  },

  // Original rigid (unskinned) variants — kept around for troubleshooting.
  // T-posed as of the latest export. Default 'none' alignment matches
  // SMPL-X motion well; a blend rule on collar bones makes the rigid
  // shoulder mesh partially follow the arm's rotation, approximating the
  // shoulder-cap deformation we get for free in skinned rigs.
  {
    id: 'female_realistic',
    label: 'Female Realistic (rigid)',
    url: '/models/female_primitive.glb',
    skinned: false,
    mapping: femaleRealisticMapping(),
    blends: blenderStudioBlends(),
    scale: 1.0,
  },
  {
    id: 'male_realistic',
    label: 'Male Realistic (rigid)',
    url: '/models/male_primitive.glb',
    skinned: false,
    mapping: blenderStudioMapping('male', 'realistic', {
      left_ankle: 'GEO-foot.005_male_primitive_realistic.L',
      right_ankle: 'GEO-foot.005_male_primitive_realistic.R',
      left_foot: 'GEO-teo_big_male_primitive_realistic.L',
      right_foot: 'GEO-teo_big_male_primitive_realistic.R',
    }),
    blends: blenderStudioBlends(),
    scale: 1.0,
  },
  {
    id: 'female_stylized',
    label: 'Female Stylized (rigid)',
    url: '/models/female_stylized.glb',
    skinned: false,
    mapping: blenderStudioMapping('female', 'stylized'),
    blends: blenderStudioBlends(),
    scale: 1.0,
  },
  {
    id: 'male_stylized',
    label: 'Male Stylized (rigid)',
    url: '/models/male_stylized.glb',
    skinned: false,
    mapping: blenderStudioMapping('male', 'stylized'),
    blends: blenderStudioBlends(),
    scale: 1.0,
  },

  // Mixamo characters — converted from FBX via web/scripts/import_mixamo_glb.py.
  // All Mixamo characters share the same skeleton, so mixamoMapping() is reused.
  {
    id: 'mixamo_y_bot',
    label: 'Y Bot (Mixamo)',
    url: '/models/mixamo_y_bot.glb',
    skinned: true,
    mapping: mixamoMapping(),
    scale: 1.0,
  },
  {
    id: 'mixamo_x_bot',
    label: 'X Bot (Mixamo)',
    url: '/models/mixamo_x_bot.glb',
    skinned: true,
    mapping: mixamoMapping(),
    scale: 1.0,
  },
  // Facepunch s&box citizen — bone naming verified against citizen.vmdl bind
  // pose. FBX loaded directly (no GLB conversion) via FBXLoader path in
  // loadCharacterRoot. Sausage / abstract variant first (citizen_lod1.fbx).
  {
    id: 'citizen_sbox',
    label: 'sbox Citizen (abstract)',
    // LOD2, not LOD1: the abstract/"sausage" body PLUS a head (CitizenHead_LOD2).
    // LOD1 deliberately has no head mesh, so it loaded headless.
    url: '/models/citizen_lod2.fbx',
    skinned: true,
    mapping: citizenMapping(),
    scale: 0.01,  // sbox units are inches; kimodo data is meters → scale 1u→1cm
  },
  {
    id: 'citizen_human_male',
    label: 'sbox Citizen (human male)',
    url: '/models/citizen_human_male.fbx',
    skinned: true,
    mapping: citizenMapping(),
    scale: 0.01,
  },
]

export function getCharacter(id) {
  return CHARACTERS.find(c => c.id === id) || CHARACTERS[0]
}

// Mixamo's standard skeleton (mixamorig:* prefix). Same naming for every
// Mixamo character — drop in any FBX downloaded from mixamo.com (T-pose,
// no animation needed) via web/scripts/import_mixamo_glb.py and reuse this
// mapping. Three.js GLTFLoader sanitizes ':' → '_' on import, so the
// retargeter's _normName strips both '.' and ':' to make the lookup match
// regardless of which form a given GLB uses.
// s&box Citizen bone mapping. Citizen bones use head→tail +X local axis
// (verified against bind pose). The 22 SMPL-X joints map cleanly onto
// citizen's primary skeleton (twist bones, fingers, helpers, IK targets
// are ignored — they're driven by the existing animgraph or stay at bind).
export function citizenMapping() {
  return {
    pelvis:          'pelvis',
    left_hip:        'leg_upper_L',  right_hip:       'leg_upper_R',
    spine1:          'spine_0',
    left_knee:       'leg_lower_L',  right_knee:      'leg_lower_R',
    spine2:          'spine_1',
    left_ankle:      'ankle_L',      right_ankle:     'ankle_R',
    spine3:          'spine_2',
    left_foot:       'ball_L',       right_foot:      'ball_R',
    neck:            'neck_0',
    left_collar:     'clavicle_L',   right_collar:    'clavicle_R',
    head:            'head',
    left_shoulder:   'arm_upper_L',  right_shoulder:  'arm_upper_R',
    left_elbow:      'arm_lower_L',  right_elbow:     'arm_lower_R',
    left_wrist:      'hand_L',       right_wrist:     'hand_R',
  }
}

export function mixamoMapping() {
  return {
    pelvis:          'mixamorig:Hips',
    left_hip:        'mixamorig:LeftUpLeg',
    right_hip:       'mixamorig:RightUpLeg',
    spine1:          'mixamorig:Spine',
    left_knee:       'mixamorig:LeftLeg',
    right_knee:      'mixamorig:RightLeg',
    spine2:          'mixamorig:Spine1',
    left_ankle:      'mixamorig:LeftFoot',
    right_ankle:     'mixamorig:RightFoot',
    spine3:          'mixamorig:Spine2',
    left_foot:       'mixamorig:LeftToeBase',
    right_foot:      'mixamorig:RightToeBase',
    neck:            'mixamorig:Neck',
    left_collar:     'mixamorig:LeftShoulder',
    right_collar:    'mixamorig:RightShoulder',
    head:            'mixamorig:Head',
    left_shoulder:   'mixamorig:LeftArm',
    right_shoulder:  'mixamorig:RightArm',
    left_elbow:      'mixamorig:LeftForeArm',
    right_elbow:     'mixamorig:RightForeArm',
    left_wrist:      'mixamorig:LeftHand',
    right_wrist:     'mixamorig:RightHand',
  }
}

function identityMapping() {
  const names = [
    'pelvis', 'left_hip', 'right_hip', 'spine1', 'left_knee', 'right_knee',
    'spine2', 'left_ankle', 'right_ankle', 'spine3', 'left_foot', 'right_foot',
    'neck', 'left_collar', 'right_collar', 'head', 'left_shoulder',
    'right_shoulder', 'left_elbow', 'right_elbow', 'left_wrist', 'right_wrist',
  ]
  return Object.fromEntries(names.map(n => [n, n]))
}

// Blend rules for rigid Blender Studio rigs: the collar mesh follows ~50% of
// the upper-arm rotation in addition to its own. Without skinning, the collar
// is a separate rigid chunk that only rotates when kimodo's collar bone
// rotates — but kimodo's collar barely moves in most motions while the arm
// swings widely. Slerping toward the arm's rotation makes the shoulder mesh
// "ride along" with the arm, mimicking how a skinned shoulder cap blends
// vertex weights between collar and upper-arm bones.
function blenderStudioBlends() {
  return {
    left_collar:  { with: 'left_shoulder',  factor: 0.5 },
    right_collar: { with: 'right_shoulder', factor: 0.5 },
  }
}

function femaleRealisticMapping() {
  return {
    pelvis:          'GEO-pelvis_female_primitive_realistic',
    left_hip:        'GEO-leg_upper_primitive_female_realistic.L',
    right_hip:       'GEO-leg_upper_primitive_female_realistic.R',
    spine1:          'GEO-belly_primitive_female_realistic',
    left_knee:       'GEO-leg_lower_primitive_female_realistic.L',
    right_knee:      'GEO-leg_lower_primitive_female_realistic.R',
    spine2:          'GEO-chest_primitive_female_realistic',
    left_ankle:      'GEO-foot_primitive_female_realistic.L',
    right_ankle:     'GEO-foot_primitive_female_realistic.R',
    // spine3 — unmapped
    left_foot:       'GEO-toe_big_primitive_female_realistic.L',
    right_foot:      'GEO-toe_big_primitive_female_realistic.R',
    neck:            'GEO-neck_primitive_female_realistic',
    left_collar:     'GEO-shoulder_primitive_female_realistic.L',
    right_collar:    'GEO-shoulder_primitive_female_realistic.R',
    head:            'GEO-head_primitive_female_realistic',
    left_shoulder:   'GEO-arm_upper_primitive_female_realistic.L',
    right_shoulder:  'GEO-arm_upper_primitive_female_realistic.R',
    left_elbow:      'GEO-arm_lower_primitive_female_realistic.L',
    right_elbow:     'GEO-arm_lower_primitive_female_realistic.R',
    left_wrist:      'GEO-hand_primitive_female_realistic.L',
    right_wrist:     'GEO-hand_primitive_female_realistic.R',
  }
}

// Blender Studio "Human Base Meshes" v1.4.1 — primitive bodies.
//
// For 3 of the 4 primitive variants the naming pattern is uniform:
//   GEO-{part}_{gender}_primitive_{flavor}[.L/.R]
//   - female stylized, male stylized, male realistic: ALL parts follow this.
//   - female realistic: same, EXCEPT the pelvis is "pelvis_female_primitive_realistic"
//     (the underscore-separated tokens are reordered).
// `overrides` lets a config patch in any per-character quirks.
function blenderStudioMapping(gender, flavor, overrides = {}) {
  // Bundle has only 2 spine joints (belly, chest); kimodo has 3, so spine3 is
  // unmapped (rotation is implicit in neck/shoulder world rotations).
  const part = (p) => `GEO-${p}_${gender}_primitive_${flavor}`
  const base = {
    pelvis:          part('pelvis'),
    left_hip:        `${part('leg_upper')}.L`,
    right_hip:       `${part('leg_upper')}.R`,
    spine1:          part('belly'),
    left_knee:       `${part('leg_lower')}.L`,
    right_knee:      `${part('leg_lower')}.R`,
    spine2:          part('chest'),
    left_ankle:      `${part('foot')}.L`,
    right_ankle:     `${part('foot')}.R`,
    // spine3 — unmapped
    left_foot:       `${part('toe_big')}.L`,
    right_foot:      `${part('toe_big')}.R`,
    neck:            part('neck'),
    left_collar:     `${part('shoulder')}.L`,
    right_collar:    `${part('shoulder')}.R`,
    head:            part('head'),
    left_shoulder:   `${part('arm_upper')}.L`,
    right_shoulder:  `${part('arm_upper')}.R`,
    left_elbow:      `${part('arm_lower')}.L`,
    right_elbow:     `${part('arm_lower')}.R`,
    left_wrist:      `${part('hand')}.L`,
    right_wrist:     `${part('hand')}.R`,
  }
  return { ...base, ...overrides }
}
