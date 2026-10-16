// The 3D view of the static site: each part extruded from its silhouette (see cabi_neat.web.WebPlanner.scene).
// Through-cuts and what joints add show; pockets, grooves and holes don't. Z is up, like the cabinet.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

const COLORS = { carcass: 0xd9b98c, front: 0xf2efe9, drawer_box: 0xc49a6c };
const HIGHLIGHT = 0x2f6fdb;

/** A 3D view in `container`. `onPick(name)` is called when a part is clicked. */
export function createViewer(container, { onPick } = {}) {
  THREE.Object3D.DEFAULT_UP.set(0, 0, 1);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  container.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(35, 1, 10, 100000);
  camera.up.set(0, 0, 1);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8170, 1.6));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(-1, -2, 3);
  scene.add(sun);

  const materials = {};
  const material = (color) => (materials[color] ??= new THREE.MeshStandardMaterial({ color, roughness: 0.75, polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 }));
  const edgeMaterial = new THREE.LineBasicMaterial({ color: 0x5b4a33, transparent: true, opacity: 0.55 });

  let group = new THREE.Group();
  scene.add(group);
  let meshes = new Map(); // part name -> {mesh, color}
  let revision = null;
  let fitted = false;

  function partObject(part) {
    const geometries = part.polygons.map(([outer, ...holes]) => {
      const shape = new THREE.Shape(outer.map(([x, y]) => new THREE.Vector2(x, y)));
      for (const hole of holes) shape.holes.push(new THREE.Path(hole.map(([x, y]) => new THREE.Vector2(x, y))));
      const geometry = new THREE.ExtrudeGeometry(shape, { depth: Math.abs(part.z1 - part.z0), bevelEnabled: false });
      geometry.translate(0, 0, Math.min(part.z0, part.z1));
      return geometry;
    });
    const f = part.frame;
    const matrix = new THREE.Matrix4().makeBasis(new THREE.Vector3(...f.xaxis), new THREE.Vector3(...f.yaxis), new THREE.Vector3(...f.zaxis));
    matrix.setPosition(...f.origin);
    if (part.matrix) matrix.premultiply(new THREE.Matrix4().set(...part.matrix.flat()));
    const object = new THREE.Group();
    const color = COLORS[part.group] ?? COLORS.carcass;
    for (const geometry of geometries) {
      const mesh = new THREE.Mesh(geometry, material(color));
      mesh.userData.part = part.name;
      object.add(mesh);
      object.add(new THREE.LineSegments(new THREE.EdgesGeometry(geometry, 20), edgeMaterial));
    }
    object.applyMatrix4(matrix);
    return { object, color };
  }

  function fit() {
    const box = new THREE.Box3().setFromObject(group);
    if (box.isEmpty()) return;
    const center = box.getCenter(new THREE.Vector3());
    const size = Math.max(...box.getSize(new THREE.Vector3()).toArray());
    camera.position.set(center.x + 1.2 * size, center.y - 2.0 * size, center.z + 0.8 * size);
    controls.target.copy(center);
    controls.update();
    fitted = true;
  }

  /** Show the parts of a scene (if its revision is new). */
  function show(data) {
    if (data.revision === revision) return;
    revision = data.revision;
    scene.remove(group);
    group.traverse((o) => o.geometry && o.geometry.dispose());
    group = new THREE.Group();
    meshes = new Map();
    for (const part of data.parts) {
      const { object, color } = partObject(part);
      group.add(object);
      meshes.set(part.name, { object, color });
    }
    scene.add(group);
    if (!fitted) fit();
  }

  /** Highlight the parts `names` (and only those). */
  function highlight(names) {
    const lit = new Set(names);
    for (const [name, { object, color }] of meshes) {
      const m = material(lit.has(name) ? HIGHLIGHT : color);
      object.traverse((o) => {
        if (o.isMesh) o.material = m;
      });
    }
  }

  // a click (not a drag) picks the part under the pointer
  const raycaster = new THREE.Raycaster();
  let down = null;
  renderer.domElement.addEventListener("pointerdown", (e) => (down = [e.clientX, e.clientY]));
  renderer.domElement.addEventListener("pointerup", (e) => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 4) return;
    const rect = renderer.domElement.getBoundingClientRect();
    const pointer = new THREE.Vector2(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
    raycaster.setFromCamera(pointer, camera);
    const hit = raycaster.intersectObjects(group.children, true).find((h) => h.object.isMesh);
    if (hit && onPick) onPick(hit.object.userData.part);
  });

  function resize() {
    const { clientWidth: w, clientHeight: h } = container;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(container);
  resize();

  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });

  return { show, highlight, fit };
}
