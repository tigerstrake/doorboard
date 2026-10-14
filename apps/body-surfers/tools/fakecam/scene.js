// Synthetic "person in a room" renderer for the fake camera (three.js).
// Shared by fakecam.js (live MediaStream) and validate.js (headless PoseLandmarker check).
//
// World: metres, y up, floor at y=0, camera on +z looking toward -z (a laptop on a desk
// ~2.6 m away). People face the camera, so a person's own LEFT is +x = right side of the raw
// (unmirrored) camera image — exactly what a real webcam sees.
import * as THREE from 'three';

// ---------------------------------------------------------------------------------------------
// Body dimensions (1.75 m adult)
const B = {
  shin: 0.43, thigh: 0.45, hipHalf: 0.095, torso: 0.49, shoulderHalf: 0.2,
  upperArm: 0.29, foreArm: 0.26, neck: 0.07,
};
const HIP_Y = 0.08 + B.shin + B.thigh; // ankle height + leg

const OUTFITS = [
  { shirt: 0xc0392b, pants: 0x2c3e66, skin: 0xd9a27e, hair: 0x2b1d14, shoes: 0x222222 },
  { shirt: 0x2e8b57, pants: 0x3a3a3a, skin: 0x9c6b4a, hair: 0x111111, shoes: 0xeeeeee },
  { shirt: 0x5b6fb5, pants: 0x6b5a45, skin: 0xe8c0a0, hair: 0x6b4423, shoes: 0x333333 },
];

const smooth = (u) => { u = Math.min(1, Math.max(0, u)); return u * u * (3 - 2 * u); };

function mat(color, rough = 0.8) {
  return new THREE.MeshStandardMaterial({ color, roughness: rough, metalness: 0 });
}

function capsule(parent, len, r, material, { x = 0, y = -len / 2, z = 0, sx = 1, sz = 1 } = {}) {
  const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, len, 6, 14), material);
  m.position.set(x, y, z);
  m.scale.set(sx, 1, sz);
  parent.add(m);
  return m;
}

function joint(parent, x, y, z = 0) {
  const g = new THREE.Group();
  g.position.set(x, y, z);
  parent.add(g);
  return g;
}

function buildHead(parent, o) {
  const skin = mat(o.skin, 0.6);
  const head = joint(parent, 0, 0.13, 0.01);
  const skull = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 24), skin);
  skull.scale.set(0.083, 0.112, 0.098);
  head.add(skull);
  // jaw / chin
  const jaw = new THREE.Mesh(new THREE.SphereGeometry(1, 24, 16), skin);
  jaw.scale.set(0.06, 0.05, 0.068);
  jaw.position.set(0, -0.05, 0.012);
  head.add(jaw);
  // hair cap (top + back)
  const hair = new THREE.Mesh(new THREE.SphereGeometry(1, 32, 16, 0, Math.PI * 2, 0, Math.PI * 0.55), mat(o.hair, 0.9));
  hair.scale.set(0.088, 0.118, 0.104);
  hair.position.set(0, 0.012, -0.008);
  hair.rotation.x = -0.35;
  head.add(hair);
  // eyes
  const white = mat(0xf4f4f0, 0.3), iris = mat(0x2a1a10, 0.3), brow = mat(o.hair, 0.9);
  for (const s of [-1, 1]) {
    const eye = new THREE.Mesh(new THREE.SphereGeometry(0.014, 16, 12), white);
    eye.scale.set(1.3, 0.8, 0.6);
    eye.position.set(s * 0.033, 0.012, 0.087);
    head.add(eye);
    const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.0075, 12, 10), iris);
    pupil.position.set(s * 0.033, 0.012, 0.0955);
    head.add(pupil);
    const b = new THREE.Mesh(new THREE.BoxGeometry(0.032, 0.007, 0.008), brow);
    b.position.set(s * 0.034, 0.036, 0.09);
    b.rotation.z = s * -0.12;
    head.add(b);
    const ear = new THREE.Mesh(new THREE.SphereGeometry(1, 12, 10), skin);
    ear.scale.set(0.012, 0.028, 0.018);
    ear.position.set(s * 0.083, 0.0, 0.0);
    head.add(ear);
  }
  // nose
  const nose = new THREE.Mesh(new THREE.ConeGeometry(0.014, 0.04, 12), skin);
  nose.rotation.x = Math.PI / 2 + 0.3;
  nose.position.set(0, -0.012, 0.102);
  head.add(nose);
  // mouth
  const mouth = new THREE.Mesh(new THREE.CapsuleGeometry(0.005, 0.03, 4, 8), mat(0x9a4a48, 0.5));
  mouth.rotation.z = Math.PI / 2;
  mouth.position.set(0, -0.052, 0.085);
  head.add(mouth);
  return head;
}

/** Build a jointed figure. Returns { root, j: joints, apply(pose) }. */
export function buildPerson(outfitIndex = 0) {
  const o = OUTFITS[outfitIndex % OUTFITS.length];
  const shirt = mat(o.shirt), pants = mat(o.pants, 0.9), skin = mat(o.skin, 0.6), shoes = mat(o.shoes, 0.7);
  const root = new THREE.Group();
  const pelvis = joint(root, 0, HIP_Y);
  // hips / pelvis block
  const hipBlock = capsule(pelvis, 0.2, 0.1, pants, { y: 0.02, sz: 0.8 });
  hipBlock.rotation.z = Math.PI / 2;
  const spine = joint(pelvis, 0, 0.05);
  // torso (shirt): tapered — waist capsule + chest capsule
  capsule(spine, 0.14, 0.13, shirt, { y: 0.1, sz: 0.68 });
  capsule(spine, 0.12, 0.16, shirt, { y: 0.28, sx: 1.05, sz: 0.62 });
  const shoulderLine = capsule(spine, 0.3, 0.075, shirt, { y: B.torso - 0.08 });
  shoulderLine.rotation.z = Math.PI / 2;
  const neckJ = joint(spine, 0, B.torso - 0.04);
  capsule(neckJ, B.neck, 0.045, skin, { y: B.neck / 2 });
  const head = buildHead(neckJ, o);

  const j = { pelvis, spine, neck: neckJ, head };
  for (const s of [-1, 1]) {
    const side = s > 0 ? 'L' : 'R'; // +x is the person's left
    const sh = joint(spine, s * B.shoulderHalf, B.torso - 0.08);
    capsule(sh, B.upperArm, 0.052, shirt);
    const el = joint(sh, 0, -B.upperArm);
    capsule(el, B.foreArm, 0.038, skin);
    const hand = new THREE.Mesh(new THREE.SphereGeometry(1, 14, 10), skin);
    hand.scale.set(0.035, 0.06, 0.022);
    hand.position.set(0, -B.foreArm - 0.06, 0);
    el.add(hand);
    const hip = joint(pelvis, s * B.hipHalf, 0);
    capsule(hip, B.thigh, 0.075, pants);
    const kn = joint(hip, 0, -B.thigh);
    capsule(kn, B.shin, 0.055, pants);
    const ank = joint(kn, 0, -B.shin);
    const shoe = new THREE.Mesh(new THREE.BoxGeometry(0.1, 0.07, 0.25), shoes);
    shoe.position.set(0, -0.045, 0.06);
    ank.add(shoe);
    Object.assign(j, { ['sh' + side]: sh, ['el' + side]: el, ['hip' + side]: hip, ['kn' + side]: kn, ['ank' + side]: ank });
  }

  function apply(p) {
    // p: { x, z, lift (jump height), squat 0..1, armsUp 0..1, yaw, walk (phase rad), walkAmp, t }
    const a = (p.squat ?? 0) * 1.05; // thigh flexion (rad)
    const drop = (B.thigh + B.shin) * (1 - Math.cos(a));
    const breathe = Math.sin((p.t ?? 0) * 1.7) * 0.004;
    const sway = Math.sin((p.t ?? 0) * 0.9) * 0.006;
    root.position.set((p.x ?? 0) + sway, (p.lift ?? 0) - drop, p.z ?? 0);
    root.rotation.y = p.yaw ?? 0;
    const walk = p.walk ?? 0, wa = p.walkAmp ?? 0;
    const tuck = p.tuck ?? 0; // knee bend while airborne
    for (const s of [-1, 1]) {
      const side = s > 0 ? 'L' : 'R';
      const legSwing = wa * 0.45 * Math.sin(walk + (s > 0 ? 0 : Math.PI));
      const stepBend = (s > 0 ? p.bendL : p.bendR) ?? 0;
      const kneeWalk = wa * 0.5 * Math.max(0, Math.sin(walk + (s > 0 ? 0 : Math.PI) + 1.2));
      j['hip' + side].rotation.set(-a - legSwing - stepBend * 0.6 - tuck * 0.5, 0, s * (0.03 + (p.spread ?? 0)));
      j['kn' + side].rotation.x = 2 * a + kneeWalk + stepBend * 1.1 + tuck * 1.0;
      j['ank' + side].rotation.x = -a + (tuck * -0.3);
      const up = p.armsUp ?? 0;
      const armSwing = wa * 0.35 * Math.sin(walk + (s > 0 ? Math.PI : 0));
      // abduct outward (+z rot for +x arm), forward reach during squat, swing while walking
      j['sh' + side].rotation.set(-(p.squat ?? 0) * 0.9 - armSwing, 0, s * (0.12 + up * 2.45 + breathe * 2));
      j['el' + side].rotation.set(-0.15 - (p.squat ?? 0) * 0.3 - wa * 0.3, 0, s * up * 0.15);
    }
    j.spine.rotation.x = a * 0.55 + breathe;
    j.neck.rotation.x = -a * 0.45;
  }
  return { root, j, apply };
}

// ---------------------------------------------------------------------------------------------
// Routines. A routine is a list of actors; each actor has a timeline of segments
// { phase, dur, fn(u, ctx) -> pose patch }. Actor x position carries across segments.
const SHOULDER_W = 0.36; // MediaPipe shoulder-landmark distance on this figure (measured)
const STEP = 1.3 * SHOULDER_W;

function idle(dur, phase = 'idle') { return { phase, dur, fn: () => ({}) }; }
function move(dur, dx, phase) {
  return {
    phase, dur, dx,
    fn: (u, c) => {
      const e = smooth((u * dur) / 0.45);
      const k = Math.sin(Math.PI * Math.min(1, u * dur / 0.45));
      // the leading leg lifts a little
      const lead = dx > 0 ? 'bendL' : 'bendR';
      return { x: c.x0 + dx * e, [lead]: 0.35 * k, spread: 0.06 * k };
    },
  };
}
function jump(phase = 'jump') {
  // 0-0.18 crouch, 0.18-0.6 airborne (apex 0.3 m), 0.6-0.78 landing absorb, rest idle
  const dur = 1.3;
  return {
    phase, dur,
    fn: (u) => {
      const t = u * dur;
      if (t < 0.18) return { squat: 0.28 * Math.sin((t / 0.18) * Math.PI / 2) };
      if (t < 0.6) { const v = (t - 0.18) / 0.42; return { lift: 4 * 0.3 * v * (1 - v), tuck: 0.35 * Math.sin(v * Math.PI), squat: 0.28 * Math.max(0, 1 - v * 4) }; }
      if (t < 0.9) return { squat: 0.25 * Math.sin(((t - 0.6) / 0.3) * Math.PI) };
      return {};
    },
  };
}
function squat(phase = 'squat') {
  const dur = 1.8;
  return {
    phase, dur,
    fn: (u) => {
      const t = u * dur;
      let s = 0;
      if (t < 0.4) s = smooth(t / 0.4); else if (t < 0.9) s = 1; else if (t < 1.3) s = 1 - smooth((t - 0.9) / 0.4);
      return { squat: s * 0.85 };
    },
  };
}
function handsUp(hold = 1.5, phase = 'hands-up') {
  const dur = hold + 0.6;
  return {
    phase, dur,
    fn: (u) => {
      const t = u * dur;
      const s = t < 0.3 ? smooth(t / 0.3) : t < 0.3 + hold ? 1 : 1 - smooth((t - 0.3 - hold) / 0.3);
      return { armsUp: s };
    },
  };
}

function soloTimeline() {
  return [
    idle(3, 'idle'),
    move(1.5, STEP, 'step-left'), // person's own left (+x, image right before mirroring)
    move(1.5, -STEP, 'return-center'),
    move(1.5, -STEP, 'step-right'),
    move(1.5, STEP, 'return-center'),
    jump('jump-1'), jump('jump-2'),
    squat('squat-1'), squat('squat-2'),
    handsUp(1.5, 'hands-up'),
    idle(1.5, 'end-idle'),
  ];
}

function walker(z, speed, span) {
  // walks back and forth across the room behind the player, forever
  const lap = (2 * span) / speed;
  return {
    phase: 'walk', dur: 9999,
    fn: (_u, c) => {
      const t = c.t;
      const k = (t % (2 * lap)) / lap; // 0..2
      const dir = k < 1 ? 1 : -1;
      const x = k < 1 ? -span + 2 * span * k : span - 2 * span * (k - 1);
      return { x, z, walk: t * speed * 5.2, walkAmp: 0.6, yaw: dir * 0.45 };
    },
  };
}

export const ROUTINES = {
  solo: () => [{ x: 0, z: 0, outfit: 0, segs: soloTimeline() }],
  duo: () => [
    { x: -0.75, z: 0, outfit: 0, segs: soloTimeline() },
    { x: 0.75, z: 0, outfit: 1, delay: 1.0, segs: soloTimeline() },
  ],
  spectator: () => [
    { x: 0, z: 0, outfit: 0, segs: soloTimeline() },
    { x: -2, z: -1.3, outfit: 2, spectator: true, segs: [walker(-1.3, 0.9, 2.0)] },
  ],
  idle: () => [{ x: 0, z: 0, outfit: 0, segs: [idle(9999, 'idle')] }],
};

function routineDuration(actors) {
  return Math.max(...actors.filter((a) => !a.spectator).map((a) => (a.delay ?? 0) + a.segs.reduce((s, g) => s + g.dur, 0)));
}

function evalActor(actor, t) {
  let tt = t - (actor.delay ?? 0);
  let x = actor.x;
  if (tt < 0) return { phase: 'pre', pose: { x, z: actor.z, t } };
  for (const seg of actor.segs) {
    if (tt < seg.dur) {
      const patch = seg.fn(tt / seg.dur, { x0: x, t });
      return { phase: seg.phase, pose: { x, z: actor.z, t, ...patch } };
    }
    tt -= seg.dur;
    x += seg.dx ?? 0;
  }
  return { phase: 'done', pose: { x, z: actor.z, t } };
}

// ---------------------------------------------------------------------------------------------
function buildRoom(scene) {
  const wall = new THREE.Mesh(new THREE.PlaneGeometry(14, 6), mat(0xcfc8bc, 0.95));
  wall.position.set(0, 3, -3.2);
  scene.add(wall);
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(14, 10), mat(0x8a7560, 0.9));
  floor.rotation.x = -Math.PI / 2;
  floor.position.set(0, 0, 0);
  scene.add(floor);
  const side = new THREE.Mesh(new THREE.PlaneGeometry(10, 6), mat(0xbab3a6, 0.95));
  side.rotation.y = Math.PI / 2;
  side.position.set(-4, 3, 0);
  scene.add(side);
  // a door and a picture frame for a bit of real-room clutter
  const door = new THREE.Mesh(new THREE.BoxGeometry(0.9, 2.05, 0.04), mat(0xe9e4da, 0.7));
  door.position.set(2.4, 1.025, -3.18);
  scene.add(door);
  const frame = new THREE.Mesh(new THREE.BoxGeometry(0.8, 0.55, 0.03), mat(0x3d5a6c, 0.6));
  frame.position.set(-1.9, 1.7, -3.18);
  scene.add(frame);
  const skirting = new THREE.Mesh(new THREE.BoxGeometry(14, 0.1, 0.03), mat(0xf0ece4, 0.7));
  skirting.position.set(0, 0.05, -3.18);
  scene.add(skirting);
}

/**
 * createWorld({ routine, width, height, camDist, camHeight, hfov, canvas? })
 * → { canvas, duration, renderAt(tSec) → { phase, actors:[{phase, head:[nx,ny]}] } }
 */
export function createWorld(opts = {}) {
  const width = opts.width ?? 1280, height = opts.height ?? 720;
  const routineName = opts.routine ?? 'solo';
  const make = ROUTINES[routineName];
  if (!make) throw new Error(`unknown routine "${routineName}" (have: ${Object.keys(ROUTINES).join(', ')})`);
  const actors = make();
  const canvas = opts.canvas ?? document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(1);
  renderer.setSize(width, height, false);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.shadowMap.enabled = true;

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0xcfc8bc);
  buildRoom(scene);
  scene.add(new THREE.HemisphereLight(0xfff6ea, 0x6a5a4a, 1.3));
  const key = new THREE.DirectionalLight(0xffffff, 2.2);
  key.position.set(1.5, 3.5, 3);
  key.castShadow = true;
  key.shadow.mapSize.set(1024, 1024);
  Object.assign(key.shadow.camera, { left: -4, right: 4, top: 4, bottom: -1, near: 0.5, far: 12 });
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xdde6ff, 0.6);
  fill.position.set(-2, 1.5, 2);
  scene.add(fill);

  const hfov = opts.hfov ?? 66;
  const aspect = width / height;
  const vfov = (2 * Math.atan(Math.tan((hfov * Math.PI) / 360) / aspect) * 180) / Math.PI;
  const camera = new THREE.PerspectiveCamera(vfov, aspect, 0.05, 50);
  const camDist = opts.camDist ?? (routineName === 'duo' ? 3.3 : 3.0);
  camera.position.set(0, opts.camHeight ?? 1.0, camDist);
  camera.lookAt(0, 0.92, 0);

  const figs = actors.map((a) => {
    const f = buildPerson(a.outfit);
    f.root.traverse((m) => { if (m.isMesh) { m.castShadow = true; m.receiveShadow = true; } });
    scene.add(f.root);
    return f;
  });
  const v = new THREE.Vector3();

  function renderAt(t) {
    const out = [];
    actors.forEach((a, i) => {
      const { phase, pose } = evalActor(a, t);
      figs[i].apply(pose);
      out.push({ phase, spectator: !!a.spectator });
    });
    scene.updateMatrixWorld(true);
    figs.forEach((f, i) => {
      f.j.head.getWorldPosition(v);
      v.project(camera);
      out[i].head = [(v.x + 1) / 2, (1 - v.y) / 2];
    });
    renderer.render(scene, camera);
    return { phase: out[0].phase, actors: out };
  }

  return { canvas, renderer, duration: routineDuration(actors), renderAt, routine: routineName };
}
