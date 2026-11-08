"""buho_export: llevar modelos de Blender fuera (Unity/web, impresión 3D, visor).

Lo usa el CLI (`optimizar`, `imprimir`, `visor`) a través de `pyExport(función, args)` y vale en los dos
modos (Blender abierto o sin ventana). Todo lo temporal va a la colección `_buho_export` y se borra al acabar
(salvo `dejar=True`); los ajustes que se tocan (render, materiales, visibilidad) se apilan y se deshacen.

- optimizar(): copia alta -> baja (Decimate o remallado + Decimate), Smart UV Project y bake en Cycles
  (normal de alta a baja, color base, rugosidad, metal, AO y, si hace falta, emisión y alfa) leyendo los
  materiales procedurales tal cual (Pointiness, atributos de máscara, ruido en coordenadas de objeto).
  Material PBR con esas imágenes y exportación a GLB (texturas dentro) o FBX + PNG.
- imprimir(): una malla cerrada en milímetros (openvdb si hay piezas sueltas, agujeros o vaciado),
  comprobaciones (manifold, normales, grosor) y STL/3MF propios (Blender 5.2 no trae exportador 3MF).
- exportar_glb(): GLB de la escena o de una selección, para el visor.
"""

import bpy
import bmesh
import math
import os
import time
import tempfile
import zipfile
import numpy as np
import mathutils
from mathutils.bvhtree import BVHTree

GEOM = {"MESH", "CURVE", "FONT", "SURFACE", "META"}
COLECCION_TMP = "_buho_export"
_PAL_NIEBLA = ("humo", "niebla", "neblina", "bruma", "vapor", "nube", "smoke", "fog", "mist", "haze", "cloud",
               "volumen", "volume")


# ======================================================================
# Estado: lo que se toca se apila y se deshace al revés; lo temporal se borra
# ======================================================================

class _Estado:
    def __init__(self):
        self.pila = [(bpy.context.preferences, "is_dirty", bpy.context.preferences.is_dirty)]
        self.deshacer = []          # funciones (p. ej. reconectar nodos de un material)
        self.col = None
        self.imagenes, self.materiales, self.mallas, self.otros = [], [], [], []

    def poner(self, obj, attr, val):
        self.pila.append((obj, attr, getattr(obj, attr)))
        setattr(obj, attr, val)

    def coleccion(self):
        if self.col is None:
            sc = bpy.context.scene
            self.col = bpy.data.collections.new(COLECCION_TMP)
            sc.collection.children.link(self.col)
        return self.col

    def objeto(self, nombre, data):
        o = bpy.data.objects.new(nombre, data)
        self.coleccion().objects.link(o)
        return o

    def restaurar(self, dejar=False):
        for f in reversed(self.deshacer):
            try:
                f()
            except Exception:
                pass
        self.deshacer.clear()
        for obj, attr, val in reversed(self.pila):
            try:
                setattr(obj, attr, val)
            except Exception:
                pass
        self.pila.clear()
        if dejar:
            for d in self.mallas:
                try:
                    if d.users == 0:
                        bpy.data.meshes.remove(d)
                except (ReferenceError, RuntimeError):
                    pass
            return
        if self.col is not None:
            for o in list(self.col.all_objects):
                data, tipo = o.data, o.type
                bpy.data.objects.remove(o, do_unlink=True)
                if data is not None and data.users == 0:
                    coleccion_datos = {"MESH": bpy.data.meshes, "CAMERA": bpy.data.cameras, "LIGHT": bpy.data.lights}.get(tipo)
                    if coleccion_datos is not None:
                        coleccion_datos.remove(data)
            bpy.data.collections.remove(self.col)
            self.col = None
        for lista, datos in ((self.mallas, bpy.data.meshes), (self.materiales, bpy.data.materials),
                             (self.imagenes, bpy.data.images)):
            for d in lista:
                try:
                    if d.users == 0 or datos is bpy.data.images:
                        datos.remove(d)
                except (ReferenceError, RuntimeError):
                    pass
        for d, datos in self.otros:
            try:
                datos.remove(d)
            except (ReferenceError, RuntimeError):
                pass


# ======================================================================
# Qué se exporta
# ======================================================================

def _palabras(n):
    t = ""
    for i, ch in enumerate(n):
        if ch.isupper() and i and n[i - 1].islower():
            t += " "
        t += ch.lower() if ch.isalnum() else " "
    return t.split()


def _es_niebla(o):
    if any(p.startswith(_PAL_NIEBLA) for p in _palabras(o.name)) or o.type == "VOLUME":
        return True
    mats = [s.material for s in getattr(o, "material_slots", []) if s.material is not None]
    if not mats:
        return False
    for m in mats:
        nt = m.node_tree if m.use_nodes else None
        if nt is None:
            return False
        sal = [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output] or \
              [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
        if not sal or sal[0].inputs["Surface"].is_linked or not sal[0].inputs["Volume"].is_linked:
            return False
    return True


def _fuentes(objetos=None, coleccion=None):
    """Objetos originales que cuentan: geometría visible (o instanciadores de colección), sin niebla ni _buho."""
    sc = bpy.context.scene
    if coleccion:
        if coleccion not in bpy.data.collections:
            raise RuntimeError("no existe la colección " + repr(coleccion))
        base = list(bpy.data.collections[coleccion].all_objects)
    elif objetos:
        falta = [n for n in objetos if n not in bpy.data.objects]
        if falta:
            raise RuntimeError("no existen los objetos: " + ", ".join(falta))
        base = [bpy.data.objects[n] for n in objetos]
    else:
        base = list(sc.objects)
    out = []
    for o in base:
        inst = o.type == "EMPTY" and o.instance_type == "COLLECTION" and o.instance_collection is not None
        if not (o.type in GEOM or inst) or o.name.startswith(("_buho", COLECCION_TMP)):
            continue
        if not objetos and not o.visible_get():
            continue
        if _es_niebla(o):
            continue
        out.append(o)
    if not out:
        raise RuntimeError("no hay objetos visibles con geometría que exportar")
    return out


def _mallas(fuentes):
    """[(malla nueva en espacio local, matriz de mundo, nombre)] con modificadores, curvas e instancias evaluados."""
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    nombres = set(o.name for o in fuentes)
    out = []
    for inst in dg.object_instances:
        raiz = inst.parent.original if (inst.is_instance and inst.parent) else inst.object.original
        ob = inst.object
        if raiz.name not in nombres or ob.type not in GEOM:
            continue
        try:
            me = bpy.data.meshes.new_from_object(ob, preserve_all_data_layers=True, depsgraph=dg)
        except RuntimeError:
            continue
        if not len(me.polygons):
            bpy.data.meshes.remove(me)
            continue
        # Materiales enlazados al objeto (no a la malla): que la copia los conserve.
        for i, s in enumerate(ob.material_slots):
            if i < len(me.materials) and s.material is not None and me.materials[i] != s.material:
                me.materials[i] = s.material
        out.append((me, inst.matrix_world.copy(), ob.original.name))
    if not out:
        raise RuntimeError("los objetos elegidos no tienen caras")
    return out


def _tris(me):
    return sum(len(p.vertices) - 2 for p in me.polygons)


def _verts_mundo(me, M):
    V = np.empty(len(me.vertices) * 3, dtype=np.float64)
    me.vertices.foreach_get("co", V)
    V = V.reshape(-1, 3)
    A = np.array(M, dtype=np.float64)
    return V @ A[:3, :3].T + A[:3, 3]


def _triangulos(me):
    me.calc_loop_triangles()
    T = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", T)
    return T.reshape(-1, 3)


# ======================================================================
# Render: GPU, cámara que encuadra, luces de estudio y hojas
# ======================================================================

def _gpu(est):
    sc = bpy.context.scene
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception:
        est.poner(sc.cycles, "device", "CPU")
        return "CPU"
    if prefs.compute_device_type != "NONE":
        try:
            prefs.get_devices()
        except Exception:
            pass
        if any(d.use and d.type != "CPU" for d in prefs.devices):
            est.poner(sc.cycles, "device", "GPU")
            return prefs.compute_device_type
    for t in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
        try:
            est.poner(prefs, "compute_device_type", t)
        except Exception:
            continue
        try:
            prefs.get_devices_for_type(t)
        except Exception:
            pass
        gpus = [d for d in prefs.devices if d.type == t]
        if gpus:
            for d in gpus:
                est.poner(d, "use", True)
            est.poner(sc.cycles, "device", "GPU")
            return t + " (" + ", ".join(d.name for d in gpus) + ")"
    est.poner(sc.cycles, "device", "CPU")
    return "CPU"


_VISTAS = {"frente": (0, -1, 0), "atras": (0, 1, 0), "derecha": (1, 0, 0), "izquierda": (-1, 0, 0),
           "arriba": (0, 0.0001, 1), "tres_cuartos": (-0.9, -1, 0.45), "corte": (1, -0.35, 0.25)}


def _camara(est, pts, vista, rx, ry):
    """Cámara temporal a la distancia mínima para que quepan los puntos (con un 10 % de margen)."""
    pts = np.asarray(pts, dtype=np.float64)
    mn, mx = pts.min(0), pts.max(0)
    centro = mathutils.Vector(((mn + mx) / 2).tolist())
    radio = max(float(np.linalg.norm(mx - mn)) / 2, 1e-4)
    d = mathutils.Vector(_VISTAS[vista]).normalized()
    cd = bpy.data.cameras.new("_buho_cam")
    cd.lens = 50
    cam = est.objeto("_buho_cam", cd)
    rot = (-d).to_track_quat("-Z", "Y")
    der, arr = rot @ mathutils.Vector((1, 0, 0)), rot @ mathutils.Vector((0, 1, 0))
    t = cd.sensor_width / (2 * cd.lens)
    th, tv = (t, t * ry / rx) if rx >= ry else (t * rx / ry, t)
    th, tv = th * 0.9, tv * 0.9
    Q = pts - np.array(centro)
    prof = Q @ np.array(d)
    dist = float(max(np.max(prof + np.abs(Q @ np.array(der)) / th), np.max(prof + np.abs(Q @ np.array(arr)) / tv)))
    dist = max(dist, radio * 0.5)
    cam.location = centro + d * dist
    cam.rotation_euler = rot.to_euler()
    cd.clip_start = max(dist / 1000, 1e-4)
    cd.clip_end = dist * 10 + radio * 4
    bpy.context.scene.camera = cam
    return cam


def _luces(est):
    for nombre, energia, rot in (("_buho_key", 4.0, (0.9, 0.2, 0.8)), ("_buho_fill", 1.5, (1.1, 0, -2.4)),
                                 ("_buho_rim", 2.5, (-0.6, 0, 3.0))):
        ld = bpy.data.lights.new(nombre, "SUN")
        ld.energy = energia
        lo = est.objeto(nombre, ld)
        lo.rotation_euler = rot


def _ocultar_escena(est):
    """Oculta en el render todo lo de la escena (geometría y luces) salvo lo temporal."""
    for o in bpy.context.scene.objects:
        if o.users_collection and any(c.name == COLECCION_TMP for c in o.users_collection):
            continue
        if not o.hide_render and (o.type in GEOM or o.type in {"LIGHT", "VOLUME", "EMPTY"}):
            est.poner(o, "hide_render", True)


def _render_png(ruta):
    sc = bpy.context.scene
    sc.render.filepath = ruta
    bpy.ops.render.render(write_still=True)
    return ruta


def _pixeles(ruta):
    im = bpy.data.images.load(ruta, check_existing=False)
    w, h = im.size
    a = np.empty(w * h * 4, dtype=np.float32)
    im.pixels.foreach_get(a)
    bpy.data.images.remove(im)
    return a.reshape(h, w, 4)


def _hoja(rutas, salida, cols):
    """Junta PNG del mismo tamaño en rejilla (izquierda a derecha, arriba abajo)."""
    ims = [_pixeles(r) for r in rutas]
    h, w = ims[0].shape[:2]
    filas = math.ceil(len(ims) / cols)
    g = 6
    W, H = cols * w + (cols + 1) * g, filas * h + (filas + 1) * g
    hoja = np.zeros((H, W, 4), dtype=np.float32)
    hoja[...] = (0.16, 0.16, 0.16, 1.0)
    for i, a in enumerate(ims):
        c, f = i % cols, i // cols
        x = g + c * (w + g)
        y0 = H - (g + f * (h + g)) - h
        hoja[y0:y0 + h, x:x + w, :3] = a[..., :3]
    img = bpy.data.images.new("_buho_hoja", W, H, alpha=True)
    img.pixels.foreach_set(hoja.ravel())
    os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
    img.filepath_raw = salida
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)


def _ajustes_render(est, motor, ancho, alto, muestras=None):
    sc = bpy.context.scene
    est.poner(sc.render, "engine", motor)
    est.poner(sc.render, "resolution_x", ancho)
    est.poner(sc.render, "resolution_y", alto)
    est.poner(sc.render, "resolution_percentage", 100)
    est.poner(sc.render, "film_transparent", False)
    im = sc.render.image_settings
    if hasattr(im, "media_type"):
        est.poner(im, "media_type", "IMAGE")
    est.poner(im, "file_format", "PNG")
    est.poner(im, "color_mode", "RGB")
    est.poner(sc.render, "filepath", sc.render.filepath)
    est.poner(sc, "camera", sc.camera)
    # Fondo neutro: un mundo gris temporal.
    w = bpy.data.worlds.new("_buho_mundo")
    est.otros.append((w, bpy.data.worlds))
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs["Color"].default_value = (0.2, 0.2, 0.21, 1)
        bg.inputs["Strength"].default_value = 1.0
    w.color = (0.2, 0.2, 0.21)
    est.poner(sc, "world", w)
    info = {"motor": motor}
    if motor == "CYCLES":
        est.poner(sc.cycles, "samples", muestras or 48)
        try:
            est.poner(sc.cycles, "use_denoising", True)
        except Exception:
            pass
        info["dispositivo"] = _gpu(est)
    return info


# ======================================================================
# Bake: cada canal del material se lleva a una Emission y se hornea como EMIT
# ======================================================================

def _salida(nt):
    outs = [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
    act = [n for n in outs if n.is_active_output]
    return (act or outs or [None])[0]


def _principled(nt):
    out = _salida(nt)
    if out is None or not out.inputs["Surface"].is_linked:
        return None, out
    cola, vistos = [out.inputs["Surface"].links[0].from_node], set()
    while cola:
        n = cola.pop(0)
        if n.type == "BSDF_PRINCIPLED":
            return n, out
        vistos.add(n.name)
        for i in n.inputs:
            for l in i.links:
                if l.from_node.name not in vistos:
                    cola.append(l.from_node)
    return None, out


_ENTRADA = {"color": "Base Color", "rugosidad": "Roughness", "metal": "Metallic", "emision": "Emission Color",
            "alfa": "Alpha"}


def _valor_canal(mat, canal):
    """(socket enlazado o None, valor constante RGBA) del canal en ese material."""
    if mat is None or not mat.use_nodes:
        base = {"color": tuple(mat.diffuse_color) if mat else (0.8, 0.8, 0.8, 1),
                "rugosidad": (mat.roughness if mat else 0.5,) * 3 + (1,),
                "metal": (mat.metallic if mat else 0.0,) * 3 + (1,), "emision": (0, 0, 0, 1), "alfa": (1, 1, 1, 1)}
        return None, base[canal]
    P, _ = _principled(mat.node_tree)
    if P is None:
        return None, _valor_canal(None, canal)[1] if canal != "color" else tuple(mat.diffuse_color)
    s = P.inputs[_ENTRADA[canal]]
    if canal == "emision":
        fuerza = P.inputs["Emission Strength"]
        if not fuerza.is_linked and fuerza.default_value <= 0:
            return None, (0, 0, 0, 1)
    if canal == "alfa" and not s.is_linked:
        tr = P.inputs.get("Transmission Weight")
        a = s.default_value
        if tr is not None and not tr.is_linked:
            a = a * (1 - 0.8 * tr.default_value)
        return None, (a, a, a, 1)
    if s.is_linked:
        return s.links[0].from_socket, None
    v = s.default_value
    if isinstance(v, float):
        return None, (v, v, v, 1)
    return None, tuple(v)


def _redirigir(est, mats, canal):
    """Conecta el canal de cada material a una Emission en la salida. Devuelve con qué deshacerlo
    (también queda en est.deshacer por si algo falla a mitad)."""
    hechos = []
    for mat in mats:
        nt = mat.node_tree
        out = _salida(nt)
        if out is None:
            out = nt.nodes.new("ShaderNodeOutputMaterial")
        sock, val = _valor_canal(mat, canal)
        em = nt.nodes.new("ShaderNodeEmission")
        em.name = "_buho_emit"
        em.inputs["Strength"].default_value = 1.0
        if sock is not None:
            nt.links.new(sock, em.inputs["Color"])
        else:
            em.inputs["Color"].default_value = val
        previo = out.inputs["Surface"].links[0].from_socket if out.inputs["Surface"].is_linked else None
        nt.links.new(em.outputs["Emission"], out.inputs["Surface"])

        def deshacer(nt=nt, em=em, out=out, previo=previo):
            nt.nodes.remove(em)
            if previo is not None:
                nt.links.new(previo, out.inputs["Surface"])
        est.deshacer.append(deshacer)
        hechos.append(deshacer)
    return hechos


def _devolver(est, hechos):
    for f in hechos:
        f()
        est.deshacer.remove(f)


def _canal_usado(mats, canal):
    for m in mats:
        sock, val = _valor_canal(m, canal)
        if sock is not None:
            return True
        if canal == "emision" and max(val[:3]) > 0.001:
            return True
        if canal == "alfa" and val[0] < 0.99:
            return True
    return False


def _hornear(tipo, imagen, nodo, nt, low, altas, ext, maxd, margen, muestras):
    sc = bpy.context.scene
    sc.cycles.samples = muestras
    nt.nodes.active = nodo
    for n in nt.nodes:
        n.select = n == nodo
    vl = bpy.context.view_layer
    for o in vl.objects:
        if o.select_get():
            o.select_set(False)
    for o in altas:
        o.select_set(True)
    low.select_set(True)
    vl.objects.active = low
    kw = dict(type=tipo, use_selected_to_active=True, cage_extrusion=ext, max_ray_distance=maxd, margin=margen,
              use_clear=True, target="IMAGE_TEXTURES", use_cage=False)
    if tipo == "NORMAL":
        kw["normal_space"] = "TANGENT"
    t0 = time.time()
    bpy.ops.object.bake(**kw)
    return round(time.time() - t0, 1)


# ======================================================================
# optimizar
# ======================================================================

def _baja(est, altas_mallas, caras, metodo, avisos):
    """Malla baja en espacio de mundo: todas las piezas juntas, remallado opcional y Decimate."""
    bm = bmesh.new()
    for me, M, _ in altas_mallas:
        tmp = me.copy()
        tmp.transform(M)
        bm.from_mesh(tmp)
        bpy.data.meshes.remove(tmp)
    if len(bm.verts):
        lo = np.min([v.co[:] for v in bm.verts[:min(len(bm.verts), 200000)]], axis=0)
        hi = np.max([v.co[:] for v in bm.verts[:min(len(bm.verts), 200000)]], axis=0)
        diag = float(np.linalg.norm(hi - lo))
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=max(diag * 1e-6, 1e-7))
    me = bpy.data.meshes.new("_bx_baja")
    bm.to_mesh(me)
    bm.free()
    est.mallas.append(me)
    low = est.objeto("_bx_baja", me)
    usado = "decimate"
    if metodo == "remallado":
        area = sum(p.area for p in me.polygons)
        voxel = math.sqrt(area / (3.0 * caras))
        md = low.modifiers.new("remallado", "REMESH")
        md.mode = "VOXEL"
        md.voxel_size = voxel
        md.use_smooth_shade = True
        _aplicar(est, low)
        usado = "remallado (vóxel %.4g m) + decimate" % voxel
    tris = _tris(low.data)
    if tris > caras:
        md = low.modifiers.new("decimate", "DECIMATE")
        md.decimate_type = "COLLAPSE"
        md.ratio = max(min(caras / tris, 1.0), 1e-4)
        md.use_collapse_triangulate = True
        _aplicar(est, low)
    else:
        avisos.append("el modelo ya tenía %d triángulos (<= %d): no se reduce" % (tris, caras))
        usado = "sin reducir" if metodo != "remallado" else usado
    low.data.materials.clear()
    for a in list(low.data.attributes):
        if not a.name.startswith(".") and a.name not in ("position", "sharp_face", "material_index"):
            try:
                low.data.attributes.remove(a)
            except RuntimeError:
                pass
    while low.data.uv_layers:
        low.data.uv_layers.remove(low.data.uv_layers[0])
    low.data.shade_smooth()
    return low, usado


def _aplicar(est, o):
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    nuevo = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
    viejo = o.data
    o.modifiers.clear()
    o.data = nuevo
    est.mallas.append(nuevo)
    if viejo.users == 0:
        bpy.data.meshes.remove(viejo)


def _uv(low, tam):
    vl = bpy.context.view_layer
    for o in vl.objects:
        if o.select_get():
            o.select_set(False)
    low.select_set(True)
    vl.objects.active = low
    low.data.uv_layers.new(name="UVMap")
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        margen = 4.0 / tam
        bpy.ops.uv.smart_project(angle_limit=math.radians(66), margin_method="FRACTION", island_margin=margen,
                                 area_weight=0.0, correct_aspect=True, scale_to_bounds=False)
        try:
            bpy.ops.uv.pack_islands(rotate=True, margin_method="FRACTION", margin=margen, shape_method="CONCAVE")
        except Exception:
            pass
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def _extrusion(altas_mallas, low):
    """Distancia de la alta a la baja (percentil 99,5) para la extrusión de la jaula y el rayo máximo."""
    me = low.data
    V = _verts_mundo(me, mathutils.Matrix.Identity(4))
    T = _triangulos(me)
    bvh = BVHTree.FromPolygons(V.tolist(), T.tolist())
    pts = np.concatenate([_verts_mundo(m, M) for m, M, _ in altas_mallas])
    lo, hi = pts.min(0), pts.max(0)
    diag = float(np.linalg.norm(hi - lo))
    if len(pts) > 60000:
        pts = pts[np.random.default_rng(1).choice(len(pts), 60000, replace=False)]
    d = []
    for p in pts:
        r = bvh.find_nearest(mathutils.Vector(p))
        if r[0] is not None:
            d.append(r[3])
    d = np.array(d) if d else np.array([diag * 0.01])
    p995 = float(np.percentile(d, 99.5))
    ext = max(p995 * 1.3, diag * 0.002)
    return ext, ext * 2.5, {"desvio_medio_m": round(float(d.mean()), 6), "desvio_p99_5_m": round(p995, 6),
                            "diagonal_m": round(diag, 4)}


def _imagen(est, nombre, tam, datos, alfa=False):
    img = bpy.data.images.new(nombre, tam, tam, alpha=alfa, float_buffer=False, is_data=datos)
    if datos:
        img.colorspace_settings.name = "Non-Color"
    img.generated_color = (0.5, 0.5, 1.0, 1.0) if "normal" in nombre else (0, 0, 0, 1)
    est.imagenes.append(img)
    return img


def _material_pbr(est, nombre, imgs, fuerza_emision):
    mat = bpy.data.materials.new(nombre)
    est.materiales.append(mat)
    mat.use_nodes = True
    mat.use_backface_culling = True  # glTF: doubleSided = false
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    P = nt.nodes.new("ShaderNodeBsdfPrincipled")
    P.location = (250, 0)
    nt.links.new(P.outputs["BSDF"], out.inputs["Surface"])
    nodos = {}
    y = 400
    for canal, img in imgs.items():
        n = nt.nodes.new("ShaderNodeTexImage")
        n.image = img
        n.name = n.label = canal
        n.location = (-500, y)
        y -= 280
        nodos[canal] = n
    if "color" in nodos:
        nt.links.new(nodos["color"].outputs["Color"], P.inputs["Base Color"])
    if "rugosidad" in nodos:
        nt.links.new(nodos["rugosidad"].outputs["Color"], P.inputs["Roughness"])
    if "metal" in nodos:
        nt.links.new(nodos["metal"].outputs["Color"], P.inputs["Metallic"])
    if "normal" in nodos:
        nm = nt.nodes.new("ShaderNodeNormalMap")
        nm.location = (-150, -500)
        nt.links.new(nodos["normal"].outputs["Color"], nm.inputs["Color"])
        nt.links.new(nm.outputs["Normal"], P.inputs["Normal"])
    if "emision" in nodos:
        nt.links.new(nodos["emision"].outputs["Color"], P.inputs["Emission Color"])
        P.inputs["Emission Strength"].default_value = fuerza_emision
    if "ao" in nodos:
        # El exportador glTF lee la oclusión de un grupo llamado «glTF Material Output».
        g = bpy.data.node_groups.get("glTF Material Output")
        if g is None:
            g = bpy.data.node_groups.new("glTF Material Output", "ShaderNodeTree")
            g.interface.new_socket("Occlusion", in_out="INPUT", socket_type="NodeSocketFloat")
            g.interface.new_socket("Thickness", in_out="INPUT", socket_type="NodeSocketFloat")
            est.otros.append((g, bpy.data.node_groups))
        gn = nt.nodes.new("ShaderNodeGroup")
        gn.node_tree = g
        gn.location = (250, -450)
        sep = nt.nodes.new("ShaderNodeSeparateColor")
        sep.location = (0, -450)
        nt.links.new(nodos["ao"].outputs["Color"], sep.inputs["Color"])
        nt.links.new(sep.outputs["Red"], gn.inputs["Occlusion"])
    return mat, nodos, P


def _guardar_png(img, ruta):
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    img.filepath_raw = ruta
    img.file_format = "PNG"
    img.save()


def optimizar(salida, caras=30000, texturas=2048, objetos=None, coleccion=None, metodo="auto", muestras_ao=64,
              pngs=False, comparar=None, formato_imagen="png", dejar=False, nombre=None):
    t_ini = time.time()
    sc = bpy.context.scene
    salida = os.path.abspath(salida)
    ext_sal = os.path.splitext(salida)[1].lower()
    if ext_sal not in (".glb", ".fbx"):
        raise RuntimeError("optimizar exporta a .glb o .fbx")
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    tam = int(texturas)
    caras = int(caras)
    nombre = nombre or os.path.splitext(os.path.basename(salida))[0]
    est = _Estado()
    avisos, tiempos = [], {}
    vl = bpy.context.view_layer
    sel_previa = [o.name for o in vl.objects if o.select_get()]
    act_previo = vl.objects.active.name if vl.objects.active else None
    try:
        fuentes = _fuentes(objetos, coleccion)
        t0 = time.time()
        altas_mallas = _mallas(fuentes)
        antes = {"objetos": len(fuentes), "piezas": len(altas_mallas),
                 "vertices": sum(len(m.vertices) for m, _, _ in altas_mallas),
                 "caras": sum(len(m.polygons) for m, _, _ in altas_mallas),
                 "triangulos": sum(_tris(m) for m, _, _ in altas_mallas)}
        # Copias de la alta con la misma matriz que el original: las coordenadas de objeto del material no cambian.
        altas = []
        gris = None
        for me, M, n in altas_mallas:
            est.mallas.append(me)
            if not me.materials or all(m is None for m in me.materials):
                if gris is None:
                    gris = bpy.data.materials.new("_bx_gris")
                    gris.use_nodes = True
                    est.materiales.append(gris)
                me.materials.clear()
                me.materials.append(gris)
            o = est.objeto("_bx_alta_" + n, me)
            o.matrix_world = M
            altas.append(o)
        mats = []
        for o in altas:
            for m in o.data.materials:
                if m is not None and m not in mats:
                    if not m.use_nodes:
                        est.poner(m, "use_nodes", True)
                    mats.append(m)
        _ocultar_escena(est)
        tiempos["copias_s"] = round(time.time() - t0, 1)

        t0 = time.time()
        metodo_real = "remallado" if metodo == "remallado" else "decimate"
        low, usado = _baja(est, altas_mallas, caras, metodo_real, avisos)
        low.name = nombre
        low.data.name = nombre
        tiempos["reducir_s"] = round(time.time() - t0, 1)
        t0 = time.time()
        _uv(low, tam)
        tiempos["uv_s"] = round(time.time() - t0, 1)
        ext, maxd, desvio = _extrusion(altas_mallas, low)

        # Canales: siempre color, rugosidad, metal, normal y AO; emisión y alfa si algún material los usa.
        canales = ["color", "rugosidad", "metal", "normal", "ao"]
        if _canal_usado(mats, "emision"):
            canales.append("emision")
        usar_alfa = _canal_usado(mats, "alfa")
        imgs = {}
        for c in canales:
            imgs[c] = _imagen(est, "%s_%s" % (nombre, c), tam, datos=c in ("rugosidad", "metal", "normal", "ao"),
                              alfa=(c == "color" and usar_alfa))
        fuerza = 1.0
        if "emision" in canales:
            fs = []
            for m in mats:
                P, _ = _principled(m.node_tree)
                if P is not None and not P.inputs["Emission Strength"].is_linked:
                    fs.append(P.inputs["Emission Strength"].default_value)
            fuerza = max(fs) if fs else 1.0
        mat_low, nodos, P_low = _material_pbr(est, nombre + "_PBR", imgs, fuerza)
        low.data.materials.append(mat_low)

        info_render = _ajustes_render(est, "CYCLES", 64, 64)
        est.poner(sc.render.bake, "margin_type", "EXTEND")
        # Que la baja no tape a la alta en la oclusión ambiental.
        for attr in ("visible_diffuse", "visible_glossy", "visible_transmission", "visible_shadow",
                     "visible_volume_scatter"):
            if hasattr(low, attr):
                est.poner(low, attr, False)
        if sc.world is not None:
            est.poner(sc.world.light_settings, "distance", desvio["diagonal_m"] * 0.08)
        margen = max(4, tam // 256)
        nt_low = mat_low.node_tree
        hornos = {}
        for c in ("color", "rugosidad", "metal", "emision"):
            if c not in canales:
                continue
            hechos = _redirigir(est, mats, c)
            hornos[c] = _hornear("EMIT", imgs[c], nodos[c], nt_low, low, altas, ext, maxd, margen, 4)
            _devolver(est, hechos)
        if usar_alfa:
            img_a = _imagen(est, nombre + "_alfa_tmp", tam, datos=True)
            n_a = nt_low.nodes.new("ShaderNodeTexImage")
            n_a.image = img_a
            hechos = _redirigir(est, mats, "alfa")
            hornos["alfa"] = _hornear("EMIT", img_a, n_a, nt_low, low, altas, ext, maxd, margen, 4)
            _devolver(est, hechos)
            A = np.empty(tam * tam * 4, dtype=np.float32)
            img_a.pixels.foreach_get(A)
            C = np.empty(tam * tam * 4, dtype=np.float32)
            imgs["color"].pixels.foreach_get(C)
            C[3::4] = A[0::4]
            imgs["color"].pixels.foreach_set(C)
            imgs["color"].update()
            nt_low.nodes.remove(n_a)
            nt_low.links.new(nodos["color"].outputs["Alpha"], P_low.inputs["Alpha"])
        hornos["normal"] = _hornear("NORMAL", imgs["normal"], nodos["normal"], nt_low, low, altas, ext, maxd, margen, 4)
        hornos["ao"] = _hornear("AO", imgs["ao"], nodos["ao"], nt_low, low, altas, ext, maxd, margen, int(muestras_ao))
        for attr in ("visible_diffuse", "visible_glossy", "visible_transmission", "visible_shadow",
                     "visible_volume_scatter"):
            if hasattr(low, attr):
                setattr(low, attr, True)
        tiempos["bake_s"] = hornos

        # Texturas a disco: junto a la salida si es FBX o con --pngs; si no, a una carpeta temporal.
        base = os.path.splitext(salida)[0]
        carpeta_tmp = None
        if ext_sal == ".fbx" or pngs:
            rutas = {c: "%s_%s.png" % (base, c) for c in imgs}
        else:
            carpeta_tmp = tempfile.mkdtemp(prefix="buho_opt_")
            rutas = {c: os.path.join(carpeta_tmp, "%s_%s.png" % (nombre, c)) for c in imgs}
        for c, img in imgs.items():
            _guardar_png(img, rutas[c])

        # Exportar solo la baja.
        t0 = time.time()
        for o in vl.objects:
            if o.select_get():
                o.select_set(False)
        low.select_set(True)
        vl.objects.active = low
        os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
        if ext_sal == ".glb":
            fmt = {"png": "AUTO", "jpg": "JPEG", "jpeg": "JPEG", "webp": "WEBP"}.get(str(formato_imagen).lower(), "AUTO")
            bpy.ops.export_scene.gltf(filepath=salida, export_format="GLB", use_selection=True, export_apply=True,
                                      export_yup=True, export_tangents=True, export_image_format=fmt,
                                      export_materials="EXPORT", export_animations=False)
        else:
            bpy.ops.export_scene.fbx(filepath=salida, use_selection=True, apply_scale_options="FBX_SCALE_ALL",
                                     use_mesh_modifiers=True, bake_space_transform=True, axis_forward="-Z",
                                     axis_up="Y", path_mode="RELATIVE", embed_textures=False)
            avisos.append("FBX: Unity solo recoge el color; asigna a mano normal, metal, rugosidad y AO con los PNG "
                          "(o usa el GLB con glTFast, que lo trae todo)")
        tiempos["exportar_s"] = round(time.time() - t0, 1)

        despues = {"vertices": len(low.data.vertices), "caras": len(low.data.polygons), "triangulos": _tris(low.data)}

        comp = None
        if comparar:
            t0 = time.time()
            comp = _comparar(est, altas, low, comparar)
            tiempos["comparacion_s"] = round(time.time() - t0, 1)

        res = {
            "exportado": salida, "bytes": os.path.getsize(salida),
            "mb": round(os.path.getsize(salida) / 1048576, 2), "metodo": usado,
            "antes": antes, "despues": despues,
            "reduccion": "%.1f %%" % (100 * (1 - despues["triangulos"] / max(antes["triangulos"], 1))),
            "texturas": {"tamano_px": tam, "canales": list(imgs),
                         "archivos": {c: r for c, r in rutas.items()} if carpeta_tmp is None else "dentro del GLB"},
            "bake": {"dispositivo": info_render.get("dispositivo"), "extrusion_m": round(ext, 5),
                     "rayo_max_m": round(maxd, 5), "margen_px": margen, **desvio},
            "tiempos": tiempos,
        }
        if comp:
            res["comparacion"] = comp
        if dejar:
            res["dejado"] = {"objeto": low.name, "coleccion": COLECCION_TMP}
        if avisos:
            res["avisos"] = avisos
        if carpeta_tmp:
            for f in os.listdir(carpeta_tmp):
                try:
                    os.remove(os.path.join(carpeta_tmp, f))
                except OSError:
                    pass
            try:
                os.rmdir(carpeta_tmp)
            except OSError:
                pass
        if dejar:
            # Lo que se deja no debe apuntar a la carpeta temporal.
            for img in imgs.values():
                img.pack()
            for o in altas:
                bpy.data.objects.remove(o, do_unlink=True)
        res["segundos"] = round(time.time() - t_ini, 1)
        return res
    finally:
        est.restaurar(dejar=dejar)
        if not dejar:
            for o in vl.objects:
                o.select_set(o.name in sel_previa)
            if act_previo and act_previo in bpy.data.objects:
                vl.objects.active = bpy.data.objects[act_previo]


def _comparar(est, altas, low, salida, ancho=640, alto=640):
    """Original (izquierda) y optimizado (derecha), en tres cuartos y de frente, con Cycles y la misma luz."""
    sc = bpy.context.scene
    pts = []
    for o in altas:
        pts.extend([(o.matrix_world @ mathutils.Vector(c))[:] for c in o.bound_box])
    _ajustes_render(est, "CYCLES", ancho, alto, 48)
    _luces(est)
    tmp = tempfile.mkdtemp(prefix="buho_cmp_")
    rutas = []
    try:
        for vista in ("tres_cuartos", "frente"):
            cam = _camara(est, pts, vista, ancho, alto)
            for cual in ("alta", "baja"):
                for o in altas:
                    o.hide_render = cual != "alta"
                low.hide_render = cual != "baja"
                rutas.append(_render_png(os.path.join(tmp, "%s_%s.png" % (vista, cual))))
            bpy.data.objects.remove(cam, do_unlink=True)
        for o in altas:
            o.hide_render = False
        low.hide_render = False
        _hoja(rutas, os.path.abspath(salida), 2)
    finally:
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
    return {"imagen": os.path.abspath(salida), "orden": "filas: tres_cuartos, frente; columnas: original, optimizado"}


# ======================================================================
# imprimir
# ======================================================================

def _soldar(V, T, tol):
    """Une vértices que coinciden (las piezas de Blender traen costuras duplicadas)."""
    q = np.round(V / tol).astype(np.int64)
    _, idx, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    return V[idx], inv[T]


def _componentes(nv, T):
    """Piezas conexas: propagación de la etiqueta mínima por vecinos + saltos de puntero (numpy, sin scipy)."""
    a = np.concatenate([T[:, 0], T[:, 1], T[:, 2]])
    b = np.concatenate([T[:, 1], T[:, 2], T[:, 0]])
    E = np.concatenate([np.stack([a, b], 1), np.stack([b, a], 1)])
    E = E[np.argsort(E[:, 0], kind="stable")]
    inicios = np.r_[0, np.nonzero(np.diff(E[:, 0]))[0] + 1]
    cabezas = E[inicios, 0]
    lab = np.arange(nv, dtype=np.int64)
    for _ in range(10000):
        m = np.minimum.reduceat(lab[E[:, 1]], inicios)
        nuevo = lab.copy()
        nuevo[cabezas] = np.minimum(nuevo[cabezas], m)
        # Que la raíz también baje: así las etiquetas viajan lejos en pocas vueltas.
        np.minimum.at(nuevo, lab[cabezas], m)
        while True:
            nn = nuevo[nuevo]
            if np.array_equal(nn, nuevo):
                break
            nuevo = nn
        if np.array_equal(nuevo, lab):
            break
        lab = nuevo
    usados = np.unique(T)
    return lab, len(np.unique(lab[usados]))


def _analisis(V, T):
    """Aristas abiertas / no manifold / normales incoherentes, piezas sueltas, volumen y área (en unidades de V)."""
    if len(T) == 0:
        return {"triangulos": 0}
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    und = np.sort(e, axis=1)
    _, cnt = np.unique(und, axis=0, return_counts=True)
    _, cnt_dir = np.unique(e, axis=0, return_counts=True)
    lab, piezas = _componentes(len(V), T)
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    cr = np.cross(v1 - v0, v2 - v0)
    area = float(np.linalg.norm(cr, axis=1).sum() / 2)
    vol_t = np.einsum("ij,ij->i", v0, np.cross(v1, v2)) / 6.0
    # Volumen con signo por pieza: negativo = normales hacia dentro.
    comp_t = lab[T[:, 0]]
    ids, inv = np.unique(comp_t, return_inverse=True)
    vol_c = np.zeros(len(ids))
    np.add.at(vol_c, inv, vol_t)
    return {"triangulos": int(len(T)), "vertices": int(len(np.unique(T))), "piezas": int(piezas),
            "aristas_abiertas": int((cnt == 1).sum()), "aristas_no_manifold": int((cnt > 2).sum()),
            "aristas_normales_incoherentes": int((cnt_dir > 1).sum()),
            "piezas_del_reves": int((vol_c < 0).sum()), "volumen": float(vol_t.sum()), "area": area}


def _ls(V, T, voxel, ex=3.0, inn=3.0):
    import openvdb as vdb
    tr = vdb.createLinearTransform(voxelSize=float(voxel))
    return vdb.FloatGrid.createLevelSetFromPolygons(np.ascontiguousarray(V, dtype=np.float32),
                                                    triangles=np.ascontiguousarray(T, dtype=np.uint32),
                                                    transform=tr, exBandWidth=float(ex), inBandWidth=float(inn))


def _iso(g, valor):
    P, Tr, Q = g.convertToPolygons(isovalue=float(valor), adaptivity=0.0)
    P = np.asarray(P, dtype=np.float64).reshape(-1, 3)
    T = _quads_a_tris(Tr, Q)
    if not len(T):
        return P, T
    tol = float(g.transform.voxelSize()[0]) * 1e-3 if hasattr(g.transform, "voxelSize") else 1e-5
    return _soldar(P, T, tol)


def _por_componente(V, T):
    """Etiqueta de pieza por triángulo y volumen con signo de cada pieza."""
    lab, _ = _componentes(len(V), T)
    comp_t = lab[T[:, 0]]
    ids, inv = np.unique(comp_t, return_inverse=True)
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    vol_t = np.einsum("ij,ij->i", v0, np.cross(v1, v2)) / 6.0
    vol = np.zeros(len(ids))
    np.add.at(vol, inv, vol_t)
    return inv.reshape(-1), vol


def _dentro_de_otra(V, T, inv, c, bvh, tri_comp):
    """¿La pieza c está dentro de otra? Paridad de cortes de un rayo con las demás piezas."""
    t = np.nonzero(inv == c)[0][0]
    o = mathutils.Vector(V[T[t]].mean(0))
    d = mathutils.Vector((0.1234, 0.2711, 0.9543)).normalized()
    cortes, eps = 0, 1e-5
    for _ in range(400):
        r = bvh.ray_cast(o, d)
        if r[0] is None:
            break
        if tri_comp[r[2]] != c:
            cortes += 1
        o = r[0] + d * eps
    return cortes % 2 == 1


def _limpiar(V, T, quitar_dentro=True, cavidad_min=0.0):
    """Quita lo que no se imprime: huecos que no son el vaciado (volumen negativo), piezas metidas dentro de
    otras (restos de piezas internas: patillas, curvas sin tapa) y cavidades del vaciado más pequeñas que
    cavidad_min (mm³)."""
    inv, vol = _por_componente(V, T)
    if vol[np.argmax(np.abs(vol))] < 0:
        T = T[:, [0, 2, 1]]
        vol = -vol
    quitar = set()
    info = {"internas": 0, "huecos_sueltos": 0, "cavidades_pequenas": 0}
    if cavidad_min <= 0:
        for c in np.nonzero(vol < 0)[0]:
            quitar.add(int(c))
            info["huecos_sueltos"] += 1
    else:
        for c in np.nonzero((vol < 0) & (-vol < cavidad_min))[0]:
            quitar.add(int(c))
            info["cavidades_pequenas"] += 1
    pos = [int(c) for c in np.argsort(-vol) if vol[c] > 0]
    if quitar_dentro and len(pos) > 1:
        bvh = BVHTree.FromPolygons(V.tolist(), T.tolist())
        for c in pos[1:]:
            if _dentro_de_otra(V, T, inv, c, bvh, inv):
                quitar.add(c)
                info["internas"] += 1
    if quitar:
        T = T[~np.isin(inv, list(quitar))]
    return V, T, info


def _vdb_malla(V, T, voxel, hueco, cerrar=0.0):
    """Una sola piel cerrada con openvdb: unión de todas las piezas, sin restos internos, cierre opcional de
    rendijas (cerrar, mm) y vaciado opcional (hueco, mm). Devuelve V, T, info y cuántos triángulos son de fuera."""
    info = {}
    if cerrar > 0:
        g = _ls(V, T, voxel, ex=3.0 + cerrar / voxel)
        P, Tr = _iso(g, cerrar)
        g = _ls(P, Tr, voxel, inn=3.0 + cerrar / voxel)
        P, Tr = _iso(g, -cerrar)
        info["rendijas_cerradas_mm"] = round(cerrar, 3)
    else:
        P, Tr = _iso(_ls(V, T, voxel), 0.0)
    P, Tr, limpieza = _limpiar(P, Tr)
    info.update({"piezas_internas_quitadas": limpieza["internas"], "huecos_internos_quitados": limpieza["huecos_sueltos"]})
    if hueco <= 0:
        return P, Tr, info, len(Tr)
    g = _ls(P, Tr, voxel, inn=hueco / voxel + 3.0)
    Pe, Te = _iso(g, 0.0)
    Pi, Ti = _iso(g, -hueco)
    if not len(Ti):
        info["sin_hueco"] = True
        return Pe, Te, info, len(Te)
    Vt = np.concatenate([Pe, Pi])
    Tt = np.concatenate([Te, Ti + len(Pe)])
    n_ext = len(Te)
    Tt = _orientar(Vt, Tt, n_ext)
    # Cavidades diminutas (en dedos, orejas…): no ahorran material y quedan atrapadas.
    Vt, Tt2, limp = _limpiar(Vt, Tt, quitar_dentro=False, cavidad_min=max(20.0, hueco ** 3 * 4))
    info["cavidades_pequenas_quitadas"] = limp["cavidades_pequenas"]
    _, vol = _por_componente(Vt, Tt2)
    info["cavidades"] = int((vol < 0).sum())
    info["triangulos_pared_interior"] = int(len(Tt2) - n_ext)
    return Vt, Tt2, info, n_ext


def _quads_a_tris(Tr, Q):
    Tr = np.asarray(Tr, dtype=np.int64).reshape(-1, 3)
    Q = np.asarray(Q, dtype=np.int64).reshape(-1, 4)
    if len(Q):
        Tr = np.concatenate([Tr, Q[:, [0, 1, 2]], Q[:, [0, 2, 3]]])
    return Tr


def _orientar(V, T, n_ext):
    """Que la piel exterior tenga volumen positivo y la interior (vaciado) negativo."""
    def vol(TT):
        v0, v1, v2 = V[TT[:, 0]], V[TT[:, 1]], V[TT[:, 2]]
        return float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)
    ext, inn = T[:n_ext], T[n_ext:]
    if vol(ext) < 0:
        ext = ext[:, [0, 2, 1]]
    if len(inn) and vol(inn) > 0:
        inn = inn[:, [0, 2, 1]]
    return np.concatenate([ext, inn]) if len(inn) else ext


def _decimar(est, V, T, maximo):
    me = bpy.data.meshes.new("_bx_imp_dec")
    me.from_pydata(V.tolist(), [], T.tolist())
    est.mallas.append(me)
    o = est.objeto("_bx_imp_dec", me)
    md = o.modifiers.new("decimate", "DECIMATE")
    md.decimate_type = "COLLAPSE"
    md.ratio = max(min(maximo / len(T), 1.0), 1e-4)
    md.use_collapse_triangulate = True
    _aplicar(est, o)
    V2 = _verts_mundo(o.data, mathutils.Matrix.Identity(4))
    T2 = _triangulos(o.data)
    bpy.data.objects.remove(o, do_unlink=True)
    return V2, T2


def _grosor(V, T, n=20000, minimo=1.0):
    """Grosor aproximado: rayos hacia dentro desde puntos de la superficie hasta la cara de enfrente."""
    bvh = BVHTree.FromPolygons(V.tolist(), T.tolist())
    rng = np.random.default_rng(7)
    idx = rng.choice(len(T), min(n, len(T)), replace=False)
    v0, v1, v2 = V[T[idx, 0]], V[T[idx, 1]], V[T[idx, 2]]
    c = (v0 + v1 + v2) / 3
    nn = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(nn, axis=1)
    ok = ln > 1e-12
    c, nn = c[ok], nn[ok] / ln[ok][:, None]
    eps = 1e-4
    d, pos = [], []
    for p, nv in zip(c, nn):
        o = mathutils.Vector(p - nv * eps)
        r = bvh.ray_cast(o, mathutils.Vector(-nv))
        if r[0] is not None:
            d.append(r[3] + eps)
            pos.append(p)
    if not d:
        return {"aviso": "no se ha podido medir el grosor"}
    d = np.array(d)
    i = int(np.argmin(d))
    return {"muestras": int(len(d)), "min_mm": round(float(d.min()), 3), "p1_mm": round(float(np.percentile(d, 1)), 3),
            "p5_mm": round(float(np.percentile(d, 5)), 3), "mediana_mm": round(float(np.median(d)), 3),
            "bajo_minimo_pct": round(float((d < minimo).mean() * 100), 2), "minimo_pedido_mm": minimo,
            "donde_mas_fino_mm": [round(float(x), 2) for x in pos[i]]}


def _escribir_stl(ruta, V, T):
    v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
    nn = np.cross(v1 - v0, v2 - v0)
    ln = np.linalg.norm(nn, axis=1)
    ln[ln == 0] = 1
    nn = nn / ln[:, None]
    dt = np.dtype([("n", "<f4", 3), ("v0", "<f4", 3), ("v1", "<f4", 3), ("v2", "<f4", 3), ("attr", "<u2")])
    a = np.zeros(len(T), dtype=dt)
    a["n"], a["v0"], a["v1"], a["v2"] = nn, v0, v1, v2
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    with open(ruta, "wb") as f:
        cab = b"buho-blender imprimir (mm)"
        f.write(cab + b" " * (80 - len(cab)))
        f.write(np.uint32(len(T)).tobytes())
        f.write(a.tobytes())
    return os.path.getsize(ruta)


def _escribir_3mf(ruta, V, T, nombre):
    """3MF mínimo (núcleo 3MF, unidades en mm): ZIP con [Content_Types].xml, _rels/.rels y 3D/3dmodel.model."""
    vs = "\n".join('<vertex x="%.4f" y="%.4f" z="%.4f"/>' % (x, y, z) for x, y, z in V)
    ts = "\n".join('<triangle v1="%d" v2="%d" v3="%d"/>' % (a, b, c) for a, b, c in T)
    nombre_xml = (nombre or "modelo").replace("&", "y").replace("<", "").replace(">", "").replace('"', "")
    modelo = ('<?xml version="1.0" encoding="UTF-8"?>\n'
              '<model unit="millimeter" xml:lang="es-ES" '
              'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
              '<metadata name="Application">buho-blender</metadata>\n'
              '<resources>\n<object id="1" type="model" name="%s">\n<mesh>\n<vertices>\n%s\n</vertices>\n'
              '<triangles>\n%s\n</triangles>\n</mesh>\n</object>\n</resources>\n'
              '<build>\n<item objectid="1"/>\n</build>\n</model>\n') % (nombre_xml, vs, ts)
    tipos = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             '</Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    with zipfile.ZipFile(ruta, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", tipos)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", modelo)
    return os.path.getsize(ruta)


def imprimir(salida, altura_mm=120.0, hueco=0.0, unir=False, objetos=None, coleccion=None, voxel_mm=None,
             caras_max=600000, grosor_min=1.0, ambos=False, captura=None, dejar=False, cerrar_mm=None):
    t_ini = time.time()
    salida = os.path.abspath(salida)
    ext = os.path.splitext(salida)[1].lower()
    if ext not in (".stl", ".3mf"):
        raise RuntimeError("imprimir exporta a .stl o .3mf")
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    altura_mm, hueco = float(altura_mm), float(hueco or 0)
    est = _Estado()
    avisos, problemas, tiempos = [], [], {}
    try:
        fuentes = _fuentes(objetos, coleccion)
        mallas = _mallas(fuentes)
        Vs, Ts, off = [], [], 0
        for me, M, _ in mallas:
            est.mallas.append(me)
            V = _verts_mundo(me, M)
            T = _triangulos(me)
            Vs.append(V)
            Ts.append(T + off)
            off += len(V)
        V = np.concatenate(Vs)
        T = np.concatenate(Ts)
        lo, hi = V.min(0), V.max(0)
        alto_m = float(hi[2] - lo[2])
        if alto_m <= 0:
            raise RuntimeError("el modelo no tiene altura (Z)")
        s = altura_mm / alto_m
        # A milímetros: apoyado en Z = 0 y centrado en XY.
        V = (V - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])) * s
        t0 = time.time()
        V, T = _soldar(V, T, 1e-4)
        antes = _analisis(V, T)
        tiempos["analisis_s"] = round(time.time() - t0, 1)
        necesita = []
        if antes["piezas"] > 1:
            necesita.append("%d piezas sueltas" % antes["piezas"])
        if antes["aristas_abiertas"]:
            necesita.append("%d aristas abiertas (agujeros)" % antes["aristas_abiertas"])
        if antes["aristas_no_manifold"]:
            necesita.append("%d aristas no manifold" % antes["aristas_no_manifold"])
        if antes["aristas_normales_incoherentes"]:
            necesita.append("%d aristas con normales incoherentes" % antes["aristas_normales_incoherentes"])
        if hueco > 0:
            necesita.append("vaciado de %.2f mm" % hueco)
        if unir:
            necesita.append("--unir")
        remallado = None
        n_ext = len(T)
        if necesita:
            t0 = time.time()
            if voxel_mm is None:
                voxel_mm = min(max(altura_mm / 600.0, 0.08), 0.5)
            voxel_mm = float(voxel_mm)
            cerrar = float(cerrar_mm) if cerrar_mm else (max(0.5, 2 * voxel_mm) if unir else 0.0)
            V, T, vinfo, n_ext = _vdb_malla(V, T, voxel_mm, hueco, cerrar)
            remallado = {"motivo": necesita, "voxel_mm": voxel_mm, "triangulos": int(len(T)), **vinfo}
            if hueco > 0 and vinfo.get("sin_hueco"):
                avisos.append("no cabe un hueco de %.2f mm: el modelo es más fino que el doble de la pared; "
                              "queda macizo" % hueco)
            tiempos["remallado_s"] = round(time.time() - t0, 1)
        T = _orientar(V, T, n_ext)
        if len(T) > caras_max:
            t0 = time.time()
            Vd, Td = _decimar(est, V, T, caras_max)
            Vd, Td = _soldar(Vd, Td, 1e-5)
            chk = _analisis(Vd, Td)
            if chk["aristas_abiertas"] == 0 and chk["aristas_no_manifold"] == 0:
                V, T = Vd, Td
                if remallado is not None:
                    remallado["decimado_a"] = int(len(T))
                else:
                    remallado = {"decimado_a": int(len(T))}
            else:
                avisos.append("el decimado rompía la malla (%d aristas abiertas, %d no manifold): se deja sin "
                              "decimar con %d triángulos" % (chk["aristas_abiertas"], chk["aristas_no_manifold"], len(T)))
            tiempos["decimado_s"] = round(time.time() - t0, 1)
        T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
        usados = np.unique(T)
        remap = np.full(len(V), -1, dtype=np.int64)
        remap[usados] = np.arange(len(usados))
        V, T = V[usados], remap[T]
        lo, hi = V.min(0), V.max(0)
        V = V - np.array([(lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, lo[2]])
        despues = _analisis(V, T)
        t0 = time.time()
        grosor = _grosor(V, T, minimo=float(grosor_min))
        tiempos["grosor_s"] = round(time.time() - t0, 1)

        # Problemas que quedan.
        if despues["aristas_abiertas"] or despues["aristas_no_manifold"]:
            problemas.append("la malla final no es cerrada: %d aristas abiertas, %d no manifold"
                             % (despues["aristas_abiertas"], despues["aristas_no_manifold"]))
        if despues["aristas_normales_incoherentes"]:
            problemas.append("%d aristas con normales incoherentes" % despues["aristas_normales_incoherentes"])
        n_cavidades = despues["piezas_del_reves"] if (hueco > 0 and remallado and remallado.get("cavidades")) else 0
        if despues["piezas_del_reves"] > n_cavidades:
            problemas.append("%d piezas con las normales hacia dentro" % (despues["piezas_del_reves"] - n_cavidades))
        piezas_sueltas = despues["piezas"] - n_cavidades
        if piezas_sueltas > 1:
            problemas.append("%d piezas que no se tocan (se imprimen sueltas o hay que unirlas a mano)" % piezas_sueltas)
        if grosor.get("min_mm") is not None and grosor["bajo_minimo_pct"] > 0.5:
            problemas.append("zonas más finas de %.2f mm (%.1f %% de la superficie, mínimo %.2f mm cerca de %s)"
                             % (grosor_min, grosor["bajo_minimo_pct"], grosor["min_mm"], grosor["donde_mas_fino_mm"]))
        if hueco > 0:
            avisos.append("vaciado sin agujeros de drenaje: en resina hay que añadirlos en el laminador")
        # Apoyo: caras que miran abajo pegadas a Z = 0.
        v0, v1, v2 = V[T[:, 0]], V[T[:, 1]], V[T[:, 2]]
        cr = np.cross(v1 - v0, v2 - v0)
        ar = np.linalg.norm(cr, axis=1) / 2
        nz = np.where(ar > 0, cr[:, 2] / np.maximum(2 * ar, 1e-12), 0)
        abajo = (np.maximum(np.maximum(v0[:, 2], v1[:, 2]), v2[:, 2]) < 0.3) & (nz < -0.9)
        apoyo = float(ar[abajo].sum())
        if apoyo < 25:
            avisos.append("apoyo en la cama de solo %.1f mm²: pon soportes o una base" % apoyo)

        archivos = {}
        nombre = os.path.splitext(os.path.basename(salida))[0]
        base = os.path.splitext(salida)[0]
        if ext == ".stl" or ambos:
            archivos["stl"] = {"ruta": base + ".stl", "bytes": _escribir_stl(base + ".stl", V, T)}
        if ext == ".3mf" or ambos:
            archivos["3mf"] = {"ruta": base + ".3mf", "bytes": _escribir_3mf(base + ".3mf", V, T, nombre)}

        tam = (V.max(0) - V.min(0)).tolist()
        res = {
            "exportado": archivos, "altura_mm": altura_mm, "escala": "%.4g mm por unidad de Blender" % s,
            "tamano_mm": [round(x, 2) for x in tam],
            "volumen_cm3": round(despues["volumen"] / 1000, 2),
            "area_cm2": round(despues["area"] / 100, 1), "apoyo_mm2": round(apoyo, 1),
            "antes": {k: v for k, v in antes.items() if k not in ("volumen", "area")},
            "despues": {k: v for k, v in despues.items() if k not in ("volumen", "area")},
            "remallado": remallado, "vaciado_mm": hueco or None, "grosor": grosor,
            "ok": not problemas, "problemas": problemas, "tiempos": tiempos,
        }
        if avisos:
            res["avisos"] = avisos
        if captura:
            t0 = time.time()
            res["captura"] = _captura_impresion(est, V, T, captura, hueco > 0)
            tiempos["captura_s"] = round(time.time() - t0, 1)
        if dejar:
            me = bpy.data.meshes.new(nombre + "_impresion")
            me.from_pydata(V.tolist(), [], T.tolist())
            o = est.objeto(nombre + "_impresion", me)
            o.scale = (0.001, 0.001, 0.001)
            res["dejado"] = {"objeto": o.name, "coleccion": COLECCION_TMP, "nota": "escala 0,001: 1 unidad = 1 mm"}
        res["segundos"] = round(time.time() - t_ini, 1)
        return res
    finally:
        est.restaurar(dejar=dejar)


def _captura_impresion(est, V, T, salida, cortar, ancho=720, alto=720):
    """Workbench: la pieza entera y, si está vaciada, cortada por la mitad para ver la pared."""
    sc = bpy.context.scene
    me = bpy.data.meshes.new("_bx_imp_vista")
    me.from_pydata(V.tolist(), [], T.tolist())
    est.mallas.append(me)
    o = est.objeto("_bx_imp_vista", me)
    _ocultar_escena(est)
    _ajustes_render(est, "BLENDER_WORKBENCH", ancho, alto)
    sh = sc.display.shading
    for attr, val in (("light", "STUDIO"), ("color_type", "SINGLE"), ("single_color", (0.75, 0.72, 0.66)),
                      ("show_cavity", True), ("show_backface_culling", False)):
        try:
            est.poner(sh, attr, val)
        except Exception:
            pass
    tmp = tempfile.mkdtemp(prefix="buho_imp_")
    rutas = []
    try:
        pts = [V.min(0), V.max(0)]
        caja = np.array([[x, y, z] for x in (pts[0][0], pts[1][0]) for y in (pts[0][1], pts[1][1])
                         for z in (pts[0][2], pts[1][2])])
        cam = _camara(est, caja, "tres_cuartos", ancho, alto)
        rutas.append(_render_png(os.path.join(tmp, "entera.png")))
        bpy.data.objects.remove(cam, do_unlink=True)
        orden = ["entera (tres cuartos)"]
        bm = bmesh.new()
        bm.from_mesh(me)
        cx = float((V[:, 0].min() + V[:, 0].max()) / 2)
        bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(cx, 0, 0),
                               plane_no=(1, 0, 0), clear_outer=True)
        me2 = bpy.data.meshes.new("_bx_imp_corte")
        bm.to_mesh(me2)
        bm.free()
        est.mallas.append(me2)
        o.data = me2
        cam = _camara(est, caja, "corte", ancho, alto)
        rutas.append(_render_png(os.path.join(tmp, "corte.png")))
        orden.append("cortada por X = centro (mirando la cara del corte: se ve la pared%s)" % ("" if cortar else "; maciza"))
        _hoja(rutas, os.path.abspath(salida), 2)
    finally:
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
    return {"imagen": os.path.abspath(salida), "orden": orden}


# ======================================================================
# visor: GLB de la escena
# ======================================================================

def exportar_glb(salida, objetos=None, coleccion=None):
    """GLB de los objetos visibles (o de la selección pedida) con sus esqueletos, para el visor."""
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    salida = os.path.abspath(salida)
    fuentes = _fuentes(objetos, coleccion)
    vl = bpy.context.view_layer
    previa = [o.name for o in vl.objects if o.select_get()]
    activo = vl.objects.active
    try:
        for o in vl.objects:
            o.select_set(False)
        extra = set()
        for o in fuentes:
            p = o.parent
            while p is not None:
                if p.type == "ARMATURE":
                    extra.add(p)
                p = p.parent
            for m in getattr(o, "modifiers", []):
                if m.type == "ARMATURE" and m.object is not None:
                    extra.add(m.object)
        for o in list(fuentes) + list(extra):
            try:
                o.select_set(True)
            except RuntimeError:
                pass
        vl.objects.active = fuentes[0]
        os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
        bpy.ops.export_scene.gltf(filepath=salida, export_format="GLB", use_selection=True, export_apply=True,
                                  export_yup=True)
    finally:
        for o in vl.objects:
            o.select_set(o.name in previa)
        vl.objects.active = activo
    res = {"glb": salida, "bytes": os.path.getsize(salida), "objetos": [o.name for o in fuentes]}
    procedurales = set()
    for o in fuentes:
        for s in getattr(o, "material_slots", []):
            m = s.material
            if m is None or not m.use_nodes:
                continue
            P, _ = _principled(m.node_tree)
            if P is None:
                procedurales.add(m.name)
                continue
            for e in ("Base Color", "Roughness", "Metallic"):
                if P.inputs[e].is_linked and P.inputs[e].links[0].from_node.type != "TEX_IMAGE":
                    procedurales.add(m.name)
    if procedurales:
        res["aviso"] = ("materiales procedurales que glTF no lleva (saldrán planos o blancos): %s. Hornéalos antes "
                        "con «optimizar» y usa ese GLB con --glb" % ", ".join(sorted(procedurales)))
    return res
