#!/usr/bin/env node
// buho-blender: CLI para trabajar con Blender desde cualquier agente (Claude, Codex, scripts).
// - Con Blender abierto habla con el complemento oficial "MCP" de Blender Lab por su puerto (9876):
//   lo que hace el agente se ve en directo en la ventana.
// - Con --fondo lanza Blender sin ventana (`blender -b`), para generar y exportar en lote.
// Sin dependencias: solo Node 18+.

import net from "node:net";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const VERSION = "0.1.0";
const HOST = process.env.BUHO_BLENDER_HOST || "127.0.0.1";
const PORT = Number(process.env.BUHO_BLENDER_PORT || 9876);
const BLENDER = process.env.BUHO_BLENDER_PATH || "D:\\Programas\\Blender\\blender.exe";
const RESULT = "__BUHO_RESULT__";
const ERROR = "__BUHO_ERROR__";

// ---------- utilidades ----------

function fail(msg, code = 1) {
  process.stderr.write(`buho-blender: ${msg}\n`);
  process.exit(code);
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--")) out[key] = true;
      else { out[key] = next; i++; }
    } else out._.push(a);
  }
  return out;
}

// Literal de Python a partir de un valor JS (strings, números, listas, null, booleanos).
const py = (v) => v === null || v === undefined ? "None" : v === true ? "True" : v === false ? "False"
  : Array.isArray(v) ? `[${v.map(py).join(", ")}]` : JSON.stringify(v);

function readCode(arg) {
  if (arg === undefined) fail("falta el código: un .py, un texto entre comillas o '-' para leer de la entrada");
  if (arg === "-") return fs.readFileSync(0, "utf8");
  if (fs.existsSync(arg) && fs.statSync(arg).isFile()) return fs.readFileSync(arg, "utf8");
  return arg;
}

// ---------- transporte: Blender abierto (puerto) ----------

function sendToOpen(code, timeoutMs = 600000) {
  return new Promise((resolve, reject) => {
    const sock = net.createConnection({ host: HOST, port: PORT });
    const chunks = [];
    const timer = setTimeout(() => { sock.destroy(); reject(new Error("Blender no contesta (tiempo agotado)")); }, timeoutMs);
    sock.on("connect", () => {
      sock.write(JSON.stringify({ type: "execute", code, strict_json: false }) + "\0");
    });
    sock.on("data", (d) => {
      chunks.push(d);
      if (d.includes(0)) sock.end();
    });
    sock.on("end", () => {
      clearTimeout(timer);
      const buf = Buffer.concat(chunks);
      const end = buf.indexOf(0);
      try { resolve(JSON.parse(buf.subarray(0, end < 0 ? buf.length : end).toString("utf8"))); }
      catch (e) { reject(new Error("respuesta no válida de Blender: " + e.message)); }
    });
    sock.on("error", (e) => {
      clearTimeout(timer);
      if (e.code === "ECONNREFUSED")
        reject(new Error(`no hay ningún Blender escuchando en ${HOST}:${PORT}. Ábrelo con "buho-blender abrir" o usa --fondo`));
      else reject(e);
    });
  });
}

// ---------- transporte: Blender sin ventana ----------

function runBackground(code, blendFile) {
  if (!fs.existsSync(BLENDER)) fail(`no encuentro Blender en ${BLENDER} (define BUHO_BLENDER_PATH)`);
  const wrapper = [
    "import json, sys, traceback",
    "_ns = {'result': {}, '__name__': '__buho__'}",
    "try:",
    `    exec(compile(${py(code)}, '<buho-blender>', 'exec'), _ns)`,
    `    print(${py(RESULT)} + json.dumps(_ns.get('result', {}), default=repr))`,
    "except Exception:",
    `    print(${py(ERROR)} + json.dumps(traceback.format_exc()))`,
    "sys.stdout.flush()",
  ].join("\n");
  const args = ["-b", "--factory-startup"];
  if (blendFile) args.push(blendFile);
  args.push("--python-exit-code", "1", "--python-expr", wrapper);
  const p = spawnSync(BLENDER, args, { encoding: "utf8", maxBuffer: 256 * 1024 * 1024 });
  const lines = (p.stdout || "").split(/\r?\n/);
  for (const l of lines) {
    if (l.startsWith(RESULT)) return { status: "ok", result: JSON.parse(l.slice(RESULT.length)) };
    if (l.startsWith(ERROR)) return { status: "error", message: JSON.parse(l.slice(ERROR.length)) };
  }
  return { status: "error", message: `Blender terminó sin resultado (código ${p.status}).\n${(p.stderr || "").slice(-2000)}` };
}

async function execute(code, opts) {
  if (opts.fondo) {
    const file = typeof opts.archivo === "string" ? path.resolve(opts.archivo) : null;
    return runBackground(code, file);
  }
  return sendToOpen(code);
}

function show(resp, opts) {
  if (resp.status !== "ok") {
    process.stderr.write(String(resp.message || JSON.stringify(resp)) + "\n");
    if (resp.stderr) process.stderr.write(String(resp.stderr) + "\n");
    process.exit(1);
  }
  if (opts.texto && resp.stdout) process.stdout.write(String(resp.stdout));
  process.stdout.write(JSON.stringify(resp.result ?? {}, null, 2) + "\n");
}

// ---------- código Python de cada comando ----------

const PY_ESTADO = `
import bpy
result = {
    "blender": bpy.app.version_string,
    "archivo": bpy.data.filepath or None,
    "sin_guardar": bpy.data.is_dirty,
    "escena": bpy.context.scene.name,
    "objetos": len(bpy.data.objects),
    "modo": bpy.context.mode,
    "motor": bpy.context.scene.render.engine,
}
`;

const PY_ESCENA = `
import bpy
def obj(o):
    d = {"nombre": o.name, "tipo": o.type, "padre": o.parent.name if o.parent else None,
         "pos": [round(v, 4) for v in o.location], "rot": [round(v, 4) for v in o.rotation_euler],
         "escala": [round(v, 4) for v in o.scale], "visible": o.visible_get()}
    if o.type == "MESH":
        d["dimensiones"] = [round(v, 4) for v in o.dimensions]
        d["caras"] = len(o.data.polygons)
        d["materiales"] = [m.name for m in o.data.materials if m]
        d["modificadores"] = [m.type for m in o.modifiers]
    return d
result = {"escena": bpy.context.scene.name, "unidades": bpy.context.scene.unit_settings.system,
          "objetos": [obj(o) for o in bpy.context.scene.objects]}
`;

function pyCaptura({ salida, vista, ancho, alto, motor, objetos, coleccion, fondoTransparente }) {
  return `
import bpy, math, mathutils
sc = bpy.context.scene
nombres = ${py(objetos)}
coleccion = ${py(coleccion)}
dentro = set(o.name for o in bpy.data.collections[coleccion].all_objects) if coleccion else None
objs = [o for o in sc.objects if o.type in {"MESH", "CURVE", "FONT"} and o.visible_get() and (not nombres or o.name in nombres) and (dentro is None or o.name in dentro)]
# Lo que no sale en la foto se oculta solo durante el render.
ocultos = [o for o in sc.objects if o.type in {"MESH", "CURVE", "FONT"} and o not in objs and not o.hide_render]
for o in ocultos:
    o.hide_render = True
if not objs:
    raise RuntimeError("no hay objetos visibles que fotografiar")
pts = [o.matrix_world @ mathutils.Vector(c) for o in objs for c in o.bound_box]
mn = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
mx = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
centro = (mn + mx) / 2
radio = max((mx - mn).length / 2, 0.01)
vistas = {"frente": (0, -1, 0), "atras": (0, 1, 0), "derecha": (1, 0, 0), "izquierda": (-1, 0, 0),
          "arriba": (0, 0, 1), "abajo": (0, 0, -1), "iso": (1, -1, 0.8), "tres_cuartos": (-0.9, -1, 0.45)}
vista = ${py(vista)}
temporales = []
viejos = {"camera": sc.camera, "engine": sc.render.engine, "x": sc.render.resolution_x, "y": sc.render.resolution_y,
          "pct": sc.render.resolution_percentage, "path": sc.render.filepath, "transp": sc.render.film_transparent,
          "fmt": sc.render.image_settings.file_format}
try:
    if vista != "camara" or sc.camera is None:
        d = mathutils.Vector(vistas.get(vista, vistas["tres_cuartos"])).normalized()
        cd = bpy.data.cameras.new("_buho_cam")
        cd.lens = 50
        cam = bpy.data.objects.new("_buho_cam", cd)
        sc.collection.objects.link(cam)
        temporales.append(cam)
        fov = 2 * math.atan(cd.sensor_width / (2 * cd.lens))
        dist = radio / math.sin(fov / 2) * 1.05
        cam.location = centro + d * dist
        cam.rotation_euler = (centro - cam.location).to_track_quat("-Z", "Y").to_euler()
        cd.clip_start = max(dist / 1000, 0.001)
        cd.clip_end = dist * 10
        sc.camera = cam
        # Luces de estudio temporales para que se vea bien aunque la escena no tenga.
        for nombre, energia, rot in (("_buho_key", 4.0, (0.9, 0.2, 0.8)), ("_buho_fill", 1.5, (1.1, 0, -2.4)), ("_buho_rim", 2.5, (-0.6, 0, 3.0))):
            ld = bpy.data.lights.new(nombre, "SUN")
            ld.energy = energia
            lo = bpy.data.objects.new(nombre, ld)
            lo.rotation_euler = rot
            sc.collection.objects.link(lo)
            temporales.append(lo)
    sc.render.engine = ${py(motor)}
    if sc.render.engine == "BLENDER_EEVEE":
        try:
            sc.eevee.taa_render_samples = 32
        except Exception:
            pass
    sc.render.resolution_x = ${ancho}
    sc.render.resolution_y = ${alto}
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = ${fondoTransparente ? "True" : "False"}
    sc.render.image_settings.file_format = "PNG"
    sc.render.filepath = ${py(salida)}
    bpy.ops.render.render(write_still=True)
finally:
    for o in ocultos:
        o.hide_render = False
    for o in temporales:
        data, tipo = o.data, o.type
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            (bpy.data.cameras if tipo == "CAMERA" else bpy.data.lights).remove(data)
    sc.camera = viejos["camera"]
    sc.render.engine = viejos["engine"]
    sc.render.resolution_x, sc.render.resolution_y = viejos["x"], viejos["y"]
    sc.render.resolution_percentage = viejos["pct"]
    sc.render.filepath = viejos["path"]
    sc.render.film_transparent = viejos["transp"]
    sc.render.image_settings.file_format = viejos["fmt"]
result = {"captura": ${py(salida)}, "vista": vista, "objetos": [o.name for o in objs],
          "tamano": [round(v, 3) for v in (mx - mn)]}
`;
}

function pyExportar({ salida, objetos, coleccion, unir }) {
  const ext = path.extname(salida).toLowerCase();
  return `
import bpy, os
nombres = ${py(objetos)}
coleccion = ${py(coleccion)}
sc = bpy.context.scene
if bpy.context.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
if coleccion:
    objs = [o for o in bpy.data.collections[coleccion].all_objects]
elif nombres:
    objs = [bpy.data.objects[n] for n in nombres]
else:
    objs = [o for o in sc.objects if o.visible_get() and o.type in {"MESH", "CURVE", "EMPTY", "ARMATURE"} and not o.name.startswith("_buho")]
unido = None
if ${unir ? "True" : "False"}:
    # Una sola malla (copias con modificadores aplicados): lo que esperan muchos motores de juego.
    dg = bpy.context.evaluated_depsgraph_get()
    import bmesh
    bm = bmesh.new()
    mats = []
    for o in objs:
        if o.type != "MESH":
            continue
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
        me.transform(o.matrix_world)
        idx = []
        for m in me.materials:
            if m not in mats:
                mats.append(m)
            idx.append(mats.index(m))
        for p in me.polygons:
            p.material_index = idx[p.material_index] if idx else 0
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    me = bpy.data.meshes.new("_buho_unido")
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    nombre = coleccion or (objs[0].name if objs else "Unido")
    unido = bpy.data.objects.new(nombre, me)
    sc.collection.objects.link(unido)
    exportados = [o.name for o in objs]
    objs = [unido]
bpy.ops.object.select_all(action="DESELECT")
for o in objs:
    o.select_set(True)
if objs:
    bpy.context.view_layer.objects.active = objs[0]
os.makedirs(os.path.dirname(${py(salida)}) or ".", exist_ok=True)
ext = ${py(ext)}
if ext in (".glb", ".gltf"):
    bpy.ops.export_scene.gltf(filepath=${py(salida)}, export_format="GLB" if ext == ".glb" else "GLTF_SEPARATE",
                              use_selection=True, export_apply=True, export_yup=True)
elif ext == ".fbx":
    bpy.ops.export_scene.fbx(filepath=${py(salida)}, use_selection=True, apply_scale_options="FBX_SCALE_ALL",
                             use_mesh_modifiers=True, bake_space_transform=True, axis_forward="-Z", axis_up="Y")
elif ext == ".obj":
    bpy.ops.wm.obj_export(filepath=${py(salida)}, export_selected_objects=True, apply_modifiers=True)
else:
    raise RuntimeError("formato no soportado: " + ext + " (usa .glb, .gltf, .fbx u .obj)")
nombres_fin = exportados if unido else [o.name for o in objs]
if unido:
    bpy.data.objects.remove(unido, do_unlink=True)
    bpy.data.meshes.remove(me)
result = {"exportado": ${py(salida)}, "objetos": nombres_fin, "unido": bool(unido), "bytes": os.path.getsize(${py(salida)})}
`;
}

// ---------- comandos ----------

const AYUDA = `buho-blender ${VERSION} — Blender para agentes (Claude, Codex, scripts)

Con Blender abierto (complemento "MCP" de Blender Lab activo, puerto ${PORT}) lo que haces se ve en directo.
Con --fondo se lanza Blender sin ventana (${BLENDER}); --archivo <x.blend> abre ese archivo antes.

  buho-blender estado                         versión, archivo abierto, nº de objetos
  buho-blender escena                         objetos con posición, tamaño, caras, materiales
  buho-blender run <script.py | "código" | ->  ejecuta Python (bpy). Para devolver datos: result = {...}
  buho-blender captura <salida.png> [--vista tres_cuartos|frente|derecha|arriba|iso|camara]
                       [--ancho 1280 --alto 720] [--motor BLENDER_EEVEE|BLENDER_WORKBENCH|CYCLES]
                       [--objetos "A,B" | --coleccion X] [--transparente]
  buho-blender exportar <salida.glb|.fbx|.obj> [--objetos "A,B" | --coleccion X] [--unir]
  buho-blender guardar <archivo.blend>        guarda la escena
  buho-blender abrir [archivo.blend]          abre Blender con ventana (el puerto se activa solo)
  buho-blender skill                          imprime la ruta de la skill (SKILL.md)

Opciones comunes: --fondo, --archivo <x.blend>, --texto (muestra también lo que imprima el script)
Variables: BUHO_BLENDER_PATH, BUHO_BLENDER_PORT, BUHO_BLENDER_HOST`;

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const [cmd, arg] = opts._;
  const lista = (v) => (typeof v === "string" ? v.split(",").map((s) => s.trim()).filter(Boolean) : []);

  switch (cmd) {
    case undefined:
    case "ayuda":
    case "help":
    case "--help":
      console.log(AYUDA);
      return;
    case "version":
      console.log(VERSION);
      return;
    case "skill": {
      const here = path.dirname(fileURLToPath(import.meta.url));
      for (const c of [path.join(here, "..", "SKILL.md"), path.join(here, "..", "skills", "buho-blender", "SKILL.md")])
        if (fs.existsSync(c)) { console.log(path.resolve(c)); return; }
      fail("no encuentro SKILL.md");
      return;
    }
    case "abrir": {
      if (!fs.existsSync(BLENDER)) fail(`no encuentro Blender en ${BLENDER}`);
      const args = arg ? [path.resolve(arg)] : [];
      spawn(BLENDER, args, { detached: true, stdio: "ignore" }).unref();
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try { await sendToOpen("result = {'ok': True}", 3000); console.log(JSON.stringify({ abierto: true, puerto: PORT })); return; }
        catch { /* sigue esperando */ }
      }
      fail("Blender se ha abierto pero el puerto no responde: activa 'Permitir acceso online' y el complemento MCP");
      return;
    }
    case "estado":
      return show(await execute(PY_ESTADO, opts), opts);
    case "escena":
      return show(await execute(PY_ESCENA, opts), opts);
    case "run":
      return show(await execute(readCode(arg), opts), opts);
    case "captura": {
      const salida = path.resolve(arg || path.join(os.tmpdir(), `buho-blender-${Date.now()}.png`));
      const code = pyCaptura({
        salida, vista: opts.vista || "tres_cuartos", ancho: Number(opts.ancho || 1280), alto: Number(opts.alto || 720),
        motor: opts.motor || "BLENDER_EEVEE", objetos: lista(opts.objetos), coleccion: typeof opts.coleccion === "string" ? opts.coleccion : null, fondoTransparente: !!opts.transparente,
      });
      return show(await execute(code, opts), opts);
    }
    case "exportar": {
      if (!arg) fail("falta la ruta de salida (.glb, .fbx u .obj)");
      const code = pyExportar({ salida: path.resolve(arg), objetos: lista(opts.objetos), coleccion: typeof opts.coleccion === "string" ? opts.coleccion : null, unir: !!opts.unir });
      return show(await execute(code, opts), opts);
    }
    case "guardar": {
      if (!arg) fail("falta la ruta del .blend");
      const code = `import bpy, os\nos.makedirs(os.path.dirname(${py(path.resolve(arg))}) or ".", exist_ok=True)\nbpy.ops.wm.save_as_mainfile(filepath=${py(path.resolve(arg))})\nresult = {"guardado": bpy.data.filepath}`;
      return show(await execute(code, opts), opts);
    }
    default:
      fail(`comando desconocido: ${cmd}\n\n${AYUDA}`);
  }
}

main().catch((e) => fail(e.message));
