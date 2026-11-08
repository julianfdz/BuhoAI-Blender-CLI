"""buho_rig: esqueletos y animaciones para buho-blender.

Lo usa el CLI (`esqueleto`, `animar`, `importar-animacion`), pero también se puede importar en un script:

    import buho_rig as br
    br.esqueleto(coleccion="FunkoJuan", tipo="humanoide")
    br.animar(accion="andar", segundos=4, bucle=True)

Convenciones:
- El personaje mira hacia -Y (como la vista «frente» de Blender), Z arriba, medidas en metros.
- Huesos humanoides: cadera, columna, pecho, cuello, cabeza, hombro/brazo/antebrazo/mano.L/.R,
  muslo/pierna/pie.L/.R. «.L» es el lado +X (izquierda del personaje, como en Rigify).
- Las rotaciones de las poses van en grados y en los ejes del esqueleto en reposo (X derecha, Y atrás,
  Z arriba), aplicadas en la articulación y relativas al hueso padre: X negativo levanta un brazo
  hacia delante, Y = -lado·ángulo lo abre hacia fuera, X positivo dobla la rodilla.
"""

import importlib
import json
import math
import os
import re

import bpy
from mathutils import Euler, Matrix, Quaternion, Vector

TIPOS_GEOM = {"MESH", "CURVE", "FONT", "SURFACE", "META"}
EXCLUIR = ("peana", "base", "suelo", "ground", "floor", "pedestal", "plinto", "soporte", "stand")

# Palabra del nombre de la pieza -> parte del cuerpo (el orden importa: lo más concreto primero).
CLAVES = [
    (("antebrazo", "forearm", "codo", "elbow"), "antebrazo"),
    (("mano", "hand", "dedo", "finger", "guante", "glove", "pulgar", "thumb", "puno", "fist"), "mano"),
    (("cuello", "neck"), "cuello"),
    (("cabeza", "head", "pelo", "hair", "oreja", "ear", "nariz", "nose", "ojo", "eye", "gafa", "gafas", "glasses",
      "marco", "cristal", "lente", "lens", "hueco", "puente", "varilla", "bisagra", "bigote", "mustache", "moustache",
      "ceja", "brow", "eyebrow", "labio", "lip", "perilla", "barba", "beard", "boca", "mouth", "sombrero", "hat",
      "gorra", "cap", "casco", "helmet", "cara", "face", "diente", "dientes", "teeth", "lengua", "tongue", "craneo",
      "skull", "frente", "mejilla", "pupila", "iris", "parpado", "pestana"), "cabeza"),
    (("manga", "sleeve", "hombro", "shoulder", "brazo", "arm", "biceps"), "brazo"),
    (("pie", "foot", "feet", "zapato", "shoe", "suela", "sole", "bota", "boot", "tobillo", "ankle", "zapatilla", "sneaker"), "pie"),
    (("espinilla", "shin", "pantorrilla", "calf", "rodilla", "knee", "gemelo"), "pierna"),
    (("muslo", "thigh", "pierna", "leg", "pantalon", "pants", "trouser"), "muslo"),
    (("cadera", "hip", "hips", "pelvis", "cintura", "waist", "cinturon", "belt"), "cadera"),
    (("torso", "cuerpo", "body", "pecho", "chest", "camiseta", "shirt", "chaqueta", "jacket", "tronco", "espalda",
      "abrigo", "coat", "jersey", "sudadera", "vestido", "dress", "chaleco", "vest", "corbata", "tie", "boton"), "pecho"),
]
LATERALES = {"brazo", "antebrazo", "mano", "muslo", "pierna", "pie"}


# ======================================================================
# Utilidades
# ======================================================================

def _palabras(nombre):
    n = re.sub(r"([a-z])([A-Z])", r"\1 \2", nombre)
    n = (n.lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o")
         .replace("ú", "u").replace("ñ", "n"))
    return [p for p in re.split(r"[^a-z0-9]+", n) if p]


def parte_de(nombre):
    """Parte del cuerpo que sugiere el nombre de una pieza (o None)."""
    pal = _palabras(nombre)
    for claves, parte in CLAVES:
        for p in pal:
            if p in claves or (p.endswith("s") and p[:-1] in claves) or (p.endswith("es") and p[:-2] in claves):
                return parte
    return None


def _excluida(o):
    return o.name.startswith("_buho") or any(p in EXCLUIR for p in _palabras(o.name))


def _caja(objs):
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        oe = o.evaluated_get(dg)
        pts += _esquinas(oe, oe.matrix_world)
    if not pts:
        raise RuntimeError("no hay geometría")
    mn = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return mn, mx


def _esquinas(oe, mw):
    """Esquinas de la caja en mundo. En curvas y textos el bound_box puede venir sin calcular (±1): se mide la malla."""
    if oe.type == "MESH":
        return [mw @ Vector(c) for c in oe.bound_box]
    try:
        me = oe.to_mesh()
    except RuntimeError:
        me = None
    if me is None or not len(me.vertices):
        if me is not None:
            oe.to_mesh_clear()
        return [mw @ Vector(c) for c in oe.bound_box]
    vs = [v.co for v in me.vertices]
    mn = Vector((min(v.x for v in vs), min(v.y for v in vs), min(v.z for v in vs)))
    mx = Vector((max(v.x for v in vs), max(v.y for v in vs), max(v.z for v in vs)))
    oe.to_mesh_clear()
    return [mw @ Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y) for z in (mn.z, mx.z)]


def _vertices(objs, maximo=60000):
    """Vértices en mundo (numpy N×3) de las mallas evaluadas, muestreados si son muchos."""
    import numpy as np
    dg = bpy.context.evaluated_depsgraph_get()
    trozos = []
    for o in objs:
        oe = o.evaluated_get(dg)
        try:
            me = oe.to_mesh()
        except RuntimeError:
            continue
        if me is None:
            continue
        n = len(me.vertices)
        if n:
            co = np.empty(n * 3, dtype=np.float64)
            me.vertices.foreach_get("co", co)
            co = co.reshape(n, 3)
            m = np.array(oe.matrix_world)
            trozos.append(co @ m[:3, :3].T + m[:3, 3])
        oe.to_mesh_clear()
    if not trozos:
        return None
    V = np.concatenate(trozos)
    if len(V) > maximo:
        V = V[np.linspace(0, len(V) - 1, maximo).astype(int)]
    return V


def _r(v):
    return [round(x, 4) for x in v]


def _contexto(obj, seleccion=None):
    """Contexto para operadores que piden objeto activo (funciona con ventana y en -b)."""
    vl = bpy.context.view_layer
    vl.objects.active = obj
    kw = {"active_object": obj, "object": obj,
          "selected_objects": seleccion or [obj], "selected_editable_objects": seleccion or [obj]}
    win = bpy.context.window
    if win is None and bpy.context.window_manager and bpy.context.window_manager.windows:
        win = bpy.context.window_manager.windows[0]
    if win is not None:
        kw["window"] = win
        area = next((a for a in win.screen.areas if a.type == "VIEW_3D"), None)
        if area is not None:
            kw["area"] = area
            kw["region"] = next((r for r in area.regions if r.type == "WINDOW"), None)
    return bpy.context.temp_override(**kw)


def _modo_objeto():
    if bpy.context.mode != "OBJECT" and bpy.context.view_layer.objects.active is not None:
        with _contexto(bpy.context.view_layer.objects.active):
            bpy.ops.object.mode_set(mode="OBJECT")


def _colgar(hijo, arm, hueso):
    mw = hijo.matrix_world.copy()
    hijo.parent = arm
    if hueso:
        hijo.parent_type = "BONE"
        hijo.parent_bone = hueso
    else:
        hijo.parent_type = "OBJECT"
    hijo.matrix_parent_inverse = Matrix.Identity(4)
    bpy.context.view_layer.update()
    hijo.matrix_world = mw


def _soltar(o):
    mw = o.matrix_world.copy()
    o.parent = None
    o.parent_type = "OBJECT"
    o.matrix_world = mw
    for m in list(o.modifiers):
        if m.type == "ARMATURE":
            o.modifiers.remove(m)


def armature(nombre=None):
    """El esqueleto indicado, el activo si es un Armature, o el único que haya."""
    if nombre:
        o = bpy.data.objects.get(nombre)
        if o is None or o.type != "ARMATURE":
            raise RuntimeError("no hay ningún esqueleto (Armature) llamado " + repr(nombre))
        return o
    act = bpy.context.view_layer.objects.active
    if act is not None and act.type == "ARMATURE":
        return act
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not arms:
        raise RuntimeError("la escena no tiene esqueleto: crea uno con 'buho-blender esqueleto'")
    if len(arms) > 1:
        raise RuntimeError("hay varios esqueletos (" + ", ".join(a.name for a in arms) + "): elige uno con --armature")
    return arms[0]


# ======================================================================
# Esqueletos
# ======================================================================

def _humanoide(objs, cabezon):
    mn, mx = _caja(objs)
    H = mx.z - mn.z
    W = mx.x - mn.x
    cx, cy = (mn.x + mx.x) / 2, (mn.y + mx.y) / 2
    z = lambda f: mn.z + f * H  # noqa: E731

    # Piezas por parte y lado
    partes = {}
    for o in objs:
        p = parte_de(o.name)
        if p is None:
            continue
        omn, omx = _caja([o])
        c = (omn + omx) / 2
        lado = "C"
        if p in LATERALES:
            lado = "L" if c.x > cx else "R"
        partes.setdefault((p, lado), []).append(o)

    def caja(p, lado="C"):
        os_ = partes.get((p, lado))
        return _caja(os_) if os_ else None

    cab = caja("cabeza")
    pec = caja("pecho")
    cue = caja("cuello")
    cad = caja("cadera")
    grande = cabezon or (cab is not None and (cab[1].z - cab[0].z) > 0.3 * H)
    if pec:
        # La columna va por el centro del torso (las gafas o la nariz no deben moverla hacia delante).
        cx, cy = (pec[0].x + pec[1].x) / 2, (pec[0].y + pec[1].y) / 2

    # Proporciones de partida (fracciones de la altura desde los pies)
    if grande:
        P = dict(tobillo=.05, rodilla=.11, cadera=.17, pecho_top=.46, cuello_top=.52, hombro_z=.43, hombro_x=.16,
                 codo=.30, muneca=.20, mano_fin=.13, pierna_x=.09)
    else:
        P = dict(tobillo=.045, rodilla=.28, cadera=.52, pecho_top=.80, cuello_top=.87, hombro_z=.80, hombro_x=.12,
                 codo=.62, muneca=.47, mano_fin=.39, pierna_x=.055)

    cadera_z = z(P["cadera"])
    pecho_top = z(P["pecho_top"])
    if pec:
        cadera_z, pecho_top = pec[0].z, pec[1].z
    if cad:
        cadera_z = min(cadera_z, cad[0].z + 0.3 * (cad[1].z - cad[0].z))
    cuello_top = max(z(P["cuello_top"]), pecho_top + 0.03 * H)
    cab_top = mx.z
    if cab:
        cab_top = cab[1].z
        cuello_top = max(pecho_top + 0.03 * H, min(cab[0].z + 0.06 * H, cab[0].z + 0.2 * (cab[1].z - cab[0].z)))
    if cue:
        cuello_top = max(cuello_top if not cab else pecho_top + 0.03 * H, cue[1].z)
    if cuello_top >= cab_top:
        cuello_top = pecho_top + 0.5 * (cab_top - pecho_top) * 0.2
    T = pecho_top - cadera_z
    c0 = Vector((cx, cy, cadera_z))
    huesos = [
        ("cadera", c0, c0 + Vector((0, 0, 0.3 * T)), None),
        ("columna", c0 + Vector((0, 0, 0.3 * T)), c0 + Vector((0, 0, 0.62 * T)), "cadera"),
        ("pecho", c0 + Vector((0, 0, 0.62 * T)), Vector((cx, cy, pecho_top)), "columna"),
        ("cuello", Vector((cx, cy, pecho_top)), Vector((cx, cy, cuello_top)), "pecho"),
        ("cabeza", Vector((cx, cy, cuello_top)), Vector((cx, cy, cab_top)), "cuello"),
    ]

    brazos_en_t = (not grande) and W > 0.75 * H and not partes.get(("mano", "L"))
    V = _vertices(objs) if not partes else None
    for s, L in ((1, "L"), (-1, "R")):
        # Hombro
        cajas_brazo = [caja(p, L) for p in ("brazo", "antebrazo")]
        cajas_brazo = [c for c in cajas_brazo if c]
        mano = caja("mano", L)
        if partes.get(("brazo", L)):
            # El hombro: centro de la pieza más alta del brazo (manga, hombro…), un poco por debajo del borde.
            bmn, bmx = _caja(partes[("brazo", L)])
            alta = max(partes[("brazo", L)], key=lambda o: _caja([o])[1].z)
            tmn, tmx = _caja([alta])
            hz = bmx.z - 0.15 * (bmx.z - bmn.z)
            if (tmx.z - tmn.z) < 0.5 * (bmx.z - bmn.z):
                hz = (tmn.z + tmx.z) / 2   # pieza corta arriba (manga, hombrera): el hombro en su centro
            hom = Vector(((tmn.x + tmx.x) / 2, (tmn.y + tmx.y) / 2, hz))
        else:
            hom = Vector((cx + s * P["hombro_x"] * H, cy, z(P["hombro_z"])))
            if pec and not grande:
                hom.x = cx + s * max(0.5 * (pec[1].x - pec[0].x) * 0.9, 0.05 * H)
        hom.z = min(hom.z, pecho_top)
        if mano:
            mc = (mano[0] + mano[1]) / 2
            tam = max(mano[1] - mano[0])
            d = (mc - hom).normalized()
            mun = mc - d * tam * 0.5
            fin = mc + d * tam * 0.5
        elif brazos_en_t:
            mun = Vector((cx + s * 0.42 * H, cy, hom.z))
            fin = Vector((mx.x if s > 0 else mn.x, cy, hom.z))
        elif cajas_brazo:
            bmn, bmx = _caja(partes.get(("brazo", L), []) + partes.get(("antebrazo", L), []))
            mun = Vector(((bmn.x + bmx.x) / 2, (bmn.y + bmx.y) / 2, bmn.z + 0.08 * (bmx.z - bmn.z)))
            fin = Vector((mun.x, mun.y, bmn.z - 0.06 * H))
        else:
            ancho = (mx.x if s > 0 else mn.x)
            mun = Vector((cx + s * min(abs(ancho - cx) * 0.9, P["hombro_x"] * H * 1.25), cy, z(P["muneca"])))
            fin = Vector((mun.x, cy, z(P["mano_fin"])))
        if V is not None and not partes.get(("brazo", L)) and not mano and not brazos_en_t:
            # Sin piezas con nombre: el brazo es la columna de vértices más exterior entre la cadera y el pecho.
            rel = s * (V[:, 0] - cx)
            banda = (V[:, 2] > z(.45)) & (V[:, 2] < z(.75)) & (rel > 0)
            if banda.any():
                xo = rel[banda].max()
                sel = (rel > xo - 0.06 * H) & (V[:, 2] > z(.3))
                if sel.sum() > 10:
                    ax, ay = cx + s * float(rel[sel].mean()), float(V[sel, 1].mean())
                    zmin, ztop = float(V[sel, 2].min()), float(V[sel, 2].max())
                    hom = Vector((ax, ay, min(hom.z, ztop - 0.03 * H)))
                    mun = Vector((ax, ay, zmin + 0.08 * H))
                    fin = Vector((ax, ay, zmin + 0.005 * H))
        codo = (hom + mun) / 2 + Vector((0, 0.02 * H, 0))
        clav = Vector((cx + s * 0.25 * abs(hom.x - cx), cy, hom.z))
        huesos += [
            ("hombro." + L, clav, hom, "pecho"),
            ("brazo." + L, hom.copy(), codo, "hombro." + L),
            ("antebrazo." + L, codo.copy(), mun, "brazo." + L),
            ("mano." + L, mun.copy(), fin, "antebrazo." + L),
        ]
        # Pierna
        pmn_pmx = caja("muslo", L)
        pie = caja("pie", L)
        if pmn_pmx:
            pmn, pmx = pmn_pmx
            cad_p = Vector(((pmn.x + pmx.x) / 2, (pmn.y + pmx.y) / 2, max(pmx.z - 0.05 * (pmx.z - pmn.z), pmn.z)))
            cad_p.z = min(cad_p.z, cadera_z + 0.04 * H) if pec else cad_p.z
        else:
            cad_p = Vector((cx + s * P["pierna_x"] * H, cy, cadera_z - (0.02 * H if not grande else 0)))
            if V is not None:
                rel = s * (V[:, 0] - cx)
                sel = (V[:, 2] < z(.3)) & (V[:, 2] > z(.08)) & (rel > 0.005 * H)
                if sel.sum() > 10:
                    cad_p.x = cx + s * float(rel[sel].mean())
                    cad_p.y = float(V[sel, 1].mean())
        if pie:
            fmn, fmx = pie
            tob = Vector(((fmn.x + fmx.x) / 2, cad_p.y if pmn_pmx else (fmn.y + fmx.y) / 2, fmx.z))
            if pmn_pmx:
                tob.y = (pmn_pmx[0].y + pmn_pmx[1].y) / 2
            punta = Vector(((fmn.x + fmx.x) / 2, fmn.y, fmn.z + 0.3 * (fmx.z - fmn.z)))
        else:
            tob = Vector((cad_p.x, cad_p.y, z(P["tobillo"])))
            punta = Vector((cad_p.x, cad_p.y - 0.07 * H, mn.z + 0.01 * H))
        if tob.z >= cad_p.z - 0.01 * H:
            tob.z = cad_p.z - 0.3 * (cad_p.z - mn.z)
        rod = (cad_p + tob) / 2 + Vector((0, -0.02 * H, 0))
        huesos += [
            ("muslo." + L, cad_p, rod, "cadera"),
            ("pierna." + L, rod.copy(), tob, "muslo." + L),
            ("pie." + L, tob.copy(), punta, "pierna." + L),
        ]
    return huesos, H


def _cuadrupedo(objs):
    mn, mx = _caja(objs)
    H, L, W = mx.z - mn.z, mx.y - mn.y, mx.x - mn.x
    cx = (mn.x + mx.x) / 2
    z = lambda f: mn.z + f * H  # noqa: E731
    y = lambda f: mn.y + f * L  # noqa: E731  (0 = morro, 1 = cola)
    huesos = [
        ("cadera", Vector((cx, y(.78), z(.62))), Vector((cx, y(.6), z(.64))), None),
        ("columna", Vector((cx, y(.6), z(.64))), Vector((cx, y(.42), z(.66))), "cadera"),
        ("pecho", Vector((cx, y(.42), z(.66))), Vector((cx, y(.25), z(.68))), "columna"),
        ("cuello", Vector((cx, y(.25), z(.68))), Vector((cx, y(.14), z(.82))), "pecho"),
        ("cabeza", Vector((cx, y(.14), z(.82))), Vector((cx, y(0), z(.80))), "cuello"),
        ("cola", Vector((cx, y(.78), z(.62))), Vector((cx, y(1.0), z(.55))), "cadera"),
    ]
    for s, Ld in ((1, "L"), (-1, "R")):
        x = cx + s * 0.3 * W
        for pref, yy, padre in (("del", .27, "pecho"), ("tras", .78, "cadera")):
            huesos += [
                ("muslo_%s.%s" % (pref, Ld), Vector((x, y(yy), z(.6))), Vector((x, y(yy), z(.34))), padre),
                ("pierna_%s.%s" % (pref, Ld), Vector((x, y(yy), z(.34))), Vector((x, y(yy), z(.06))), "muslo_%s.%s" % (pref, Ld)),
                ("pie_%s.%s" % (pref, Ld), Vector((x, y(yy), z(.06))), Vector((x, y(yy) - 0.06 * L, z(0))), "pierna_%s.%s" % (pref, Ld)),
            ]
    return huesos, H


def _cadena(objs, n):
    mn, mx = _caja(objs)
    tam = mx - mn
    eje = max(range(3), key=lambda i: tam[i])
    c = (mn + mx) / 2
    a, b = c.copy(), c.copy()
    a[eje], b[eje] = mn[eje], mx[eje]
    huesos = []
    for i in range(n):
        h = a.lerp(b, i / n)
        t = a.lerp(b, (i + 1) / n)
        huesos.append(("cadena.%02d" % (i + 1), h, t, ("cadena.%02d" % i) if i else None))
    return huesos, tam.z or max(tam)


def _desde_json(ruta):
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    lista = datos["huesos"] if isinstance(datos, dict) else datos
    huesos = []
    for h in lista:
        huesos.append((h["nombre"], Vector(h["cabeza"]), Vector(h["cola"]), h.get("padre")))
    zs = [v.z for h in huesos for v in (h[1], h[2])]
    return huesos, (max(zs) - min(zs)) or 1.0


def _crear(nombre, huesos, coleccion):
    data = bpy.data.armatures.new(nombre)
    arm = bpy.data.objects.new(nombre, data)
    coleccion.objects.link(arm)
    arm.show_in_front = True
    data.display_type = "OCTAHEDRAL"
    bpy.context.view_layer.update()
    with _contexto(arm):
        bpy.ops.object.mode_set(mode="EDIT")
        eb = data.edit_bones
        creados = {}
        for nom, cab, col, padre in huesos:
            if (col - cab).length < 1e-4:
                col = cab + Vector((0, 0, 0.01))
            b = eb.new(nom)
            b.head, b.tail = cab, col
            d = (col - cab).normalized()
            b.align_roll(Vector((0, 0, 1)) if abs(d.y) > 0.8 else Vector((0, -1, 0)))
            creados[nom] = b
        for nom, cab, col, padre in huesos:
            if padre:
                if padre not in creados:
                    raise RuntimeError("el hueso %s tiene un padre que no existe: %s" % (nom, padre))
                creados[nom].parent = creados[padre]
                creados[nom].use_connect = (creados[padre].tail - cab).length < 1e-4
        bpy.ops.object.mode_set(mode="OBJECT")
    return arm


def _distancia_segmento(p, a, b):
    ab = b - a
    t = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-12)))
    return (a + ab * t - p).length


def _hueso_cercano(arm, p):
    mw = arm.matrix_world
    return min(arm.data.bones, key=lambda b: _distancia_segmento(p, mw @ b.head_local, mw @ b.tail_local)).name


def _quitar_esqueleto(nombre):
    viejo = bpy.data.objects.get(nombre)
    if viejo is None:
        return
    for o in list(bpy.data.objects):
        if o.parent == viejo:
            _soltar(o)
        for m in list(getattr(o, "modifiers", [])):
            if m.type == "ARMATURE" and m.object == viejo:
                o.modifiers.remove(m)
    data = viejo.data
    bpy.data.objects.remove(viejo, do_unlink=True)
    if data.users == 0:
        bpy.data.armatures.remove(data)


def esqueleto(objeto=None, coleccion=None, tipo="humanoide", auto=False, huesos=None, nombre=None,
              cabezon=False, n=4, sin_emparentar=False):
    """Crea un Armature ajustado al modelo y le cuelga las mallas. Devuelve un dict para el JSON."""
    _modo_objeto()
    sc = bpy.context.scene
    if objeto:
        o = bpy.data.objects.get(objeto)
        if o is None:
            raise RuntimeError("no existe el objeto " + repr(objeto))
        objs = [o] if o.type in TIPOS_GEOM else []
        objs += [c for c in o.children_recursive if c.type in TIPOS_GEOM]
        destino = o.users_collection[0] if o.users_collection else sc.collection
    elif coleccion:
        if coleccion not in bpy.data.collections:
            raise RuntimeError("no existe la colección " + repr(coleccion))
        col = bpy.data.collections[coleccion]
        objs = [o for o in col.all_objects if o.type in TIPOS_GEOM and not _excluida(o)]
        destino = col
    else:
        objs = [o for o in sc.objects if o.type in TIPOS_GEOM and o.visible_get() and not _excluida(o)]
        destino = sc.collection
    if not objs:
        raise RuntimeError("no hay mallas a las que poner esqueleto")
    nombre = nombre or ((objeto or coleccion or "Modelo") + "_Esqueleto")
    _quitar_esqueleto(nombre)
    for o in objs:
        for m in list(o.modifiers) if hasattr(o, "modifiers") else []:
            if m.type == "ARMATURE" and m.object is None:
                o.modifiers.remove(m)

    if huesos:
        lista, H = _desde_json(huesos)
        tipo = "json"
    elif tipo == "humanoide":
        lista, H = _humanoide(objs, cabezon)
    elif tipo == "cuadrupedo":
        lista, H = _cuadrupedo(objs)
    elif tipo == "cadena":
        lista, H = _cadena(objs, int(n))
    else:
        raise RuntimeError("tipo de esqueleto desconocido: %s (humanoide, cuadrupedo, cadena)" % tipo)
    arm = _crear(nombre, lista, destino)
    arm["buho_tipo"] = tipo
    arm["buho_altura"] = float(H)

    asignacion, avisos = {}, []
    modo = "ninguno"
    if not sin_emparentar:
        mallas = [o for o in objs if o.type == "MESH"]
        raiz = [o for o in objs if o.parent is None or o.parent not in objs]
        if auto or (len(objs) == 1 and objs[0].type == "MESH"):
            modo = "pesos_automaticos"
            for o in raiz:
                if o.parent is not None:
                    _soltar(o)
            sel = [o for o in raiz if o.type == "MESH"]
            if sel:
                for o in bpy.context.view_layer.objects:
                    o.select_set(False)
                for o in sel:
                    o.select_set(True)
                arm.select_set(True)
                with _contexto(arm, sel + [arm]):
                    bpy.ops.object.parent_set(type="ARMATURE_AUTO")
                for o in sel:
                    vacios = [g.name for g in o.vertex_groups if not _grupo_con_pesos(o, g.index)]
                    asignacion[o.name] = "pesos"
                    if len(vacios) == len(o.vertex_groups):
                        avisos.append("%s: el cálculo de pesos ha fallado (malla no cerrada o con piezas sueltas); prueba sin --auto (por piezas)" % o.name)
            for o in raiz:
                if o.type != "MESH":
                    mn, mx = _caja([o])
                    b = _hueso_por_parte(arm, o, (mn + mx) / 2)
                    _colgar(o, arm, b)
                    asignacion[o.name] = b
        else:
            modo = "piezas"
            for o in raiz:
                mn, mx = _caja([o])
                b = _hueso_por_parte(arm, o, (mn + mx) / 2)
                _colgar(o, arm, b)
                asignacion[o.name] = b
            del mallas
    usados = set(v for v in asignacion.values() if v != "pesos")
    if modo == "piezas":
        # Huesos sin piezas que, al doblarse, separarían una pieza rígida (codo y rodilla): las animaciones no los giran.
        rigidos = [b.name for b in arm.data.bones if b.name.split(".")[0] in ("antebrazo", "pierna") and b.name not in usados]
        arm["buho_rigidos"] = rigidos
    else:
        arm["buho_rigidos"] = []
    bpy.context.view_layer.update()
    return {
        "esqueleto": arm.name, "tipo": tipo, "modo": modo, "altura_m": round(H, 4),
        "huesos": [{"nombre": b.name, "cabeza": _r(arm.matrix_world @ b.head_local), "cola": _r(arm.matrix_world @ b.tail_local),
                    "padre": b.parent.name if b.parent else None} for b in arm.data.bones],
        "piezas": asignacion, "sin_doblar": list(arm["buho_rigidos"]), "avisos": avisos,
        "excluidas": [o.name for o in (bpy.data.collections[coleccion].all_objects if coleccion else [])
                      if o.type in TIPOS_GEOM and _excluida(o)],
    }


def _grupo_con_pesos(o, idx):
    for v in o.data.vertices:
        for g in v.groups:
            if g.group == idx and g.weight > 0.01:
                return True
    return False


def _hueso_por_parte(arm, o, centro):
    p = parte_de(o.name)
    nombres = set(b.name for b in arm.data.bones)
    if p:
        lado = "L" if (arm.matrix_world.inverted() @ centro).x > 0 else "R"
        cand = p + "." + lado if p in LATERALES else p
        if cand in nombres:
            return cand
        # Si el esqueleto no tiene ese hueso (cuadrúpedo, json), por cercanía
    return _hueso_cercano(arm, centro)


# ======================================================================
# Animaciones
# ======================================================================

def _poses_predefinidas(accion, lado):
    """Devuelve (duración base en s, bucle natural, [(t, {hueso: (x,y,z)}, {hueso: (x,y,z) en alturas})])."""
    s = 1 if lado == "L" else -1
    So = "L" if s > 0 else "R"      # lado que hace el gesto
    Ot = "R" if s > 0 else "L"
    def lad(L):  # signo del lado L/R
        return 1 if L == "L" else -1

    if accion == "andar":
        def paso(a, b):  # a = lado que adelanta la pierna
            sa = lad(a)
            return {
                "muslo." + a: (-25, 0, 0), "muslo." + b: (20, 0, 0),
                "pierna." + a: (5, 0, 0), "pierna." + b: (30, 0, 0),
                "pie." + a: (-10, 0, 0), "pie." + b: (15, 0, 0),
                "brazo." + a: (18, 0, 0), "brazo." + b: (-18, 0, 0),
                "antebrazo." + a: (-10, 0, 0), "antebrazo." + b: (-20, 0, 0),
                "columna": (0, 0, sa * 5), "pecho": (0, 0, -sa * 4), "cadera": (0, 0, -sa * 4),
            }
        def paso_medio(a, b):
            return {"muslo." + a: (0, 0, 0), "muslo." + b: (-10, 0, 0), "pierna." + b: (45, 0, 0), "pie." + b: (10, 0, 0),
                    "antebrazo." + a: (-15, 0, 0), "antebrazo." + b: (-15, 0, 0)}
        return 1.0, True, [
            (0.0, paso("L", "R"), {"cadera": (0, 0, 0)}),
            (0.25, paso_medio("L", "R"), {"cadera": (0, 0, 0.02)}),
            (0.5, paso("R", "L"), {"cadera": (0, 0, 0)}),
            (0.75, paso_medio("R", "L"), {"cadera": (0, 0, 0.02)}),
            (1.0, paso("L", "R"), {"cadera": (0, 0, 0)}),
        ]

    if accion in ("punetazos", "puñetazos", "punetazo"):
        def guardia(extra=None):
            d = {}
            for L in ("L", "R"):
                g = lad(L)
                d["brazo." + L] = (-55, 0, -g * 15)
                d["antebrazo." + L] = (-60, 0, 0)
                d["mano." + L] = (0, 0, 0)
            d["columna"] = (0, 0, 0)
            d["pecho"] = (8, 0, 0)
            d["cabeza"] = (-6, 0, 0)
            d["muslo.L"] = (-8, 0, 0)
            d["muslo.R"] = (6, 0, 0)
            if extra:
                d.update(extra)
            return d
        def jab(L, fuerza=1.0):
            g = lad(L)
            return guardia({"brazo." + L: (-90, 0, -g * 10), "antebrazo." + L: (0, 0, 0),
                            "columna": (0, 0, -g * 15 * fuerza), "pecho": (8, 0, -g * 8 * fuerza)})
        def gancho(L):
            g = lad(L)
            return guardia({"brazo." + L: (-80, -g * 55, -g * 40), "antebrazo." + L: (-30, 0, 0),
                            "columna": (0, 0, -g * 25), "pecho": (8, 0, -g * 10)})
        base = {"cadera": (0, 0, -0.01)}
        return 2.4, True, [
            (0.0, guardia(), base),
            (0.10, jab(So), {"cadera": (0, -0.01, -0.01)}),
            (0.22, guardia(), base),
            (0.34, jab(Ot), {"cadera": (0, -0.01, -0.01)}),
            (0.46, guardia(), base),
            (0.58, jab(So, 1.6), {"cadera": (0, -0.025, -0.015)}),
            (0.70, guardia(), base),
            (0.84, gancho(Ot), {"cadera": (0, -0.01, -0.015)}),
            (1.0, guardia(), base),
        ]

    if accion == "saludar":
        arriba = lambda a, b: {"brazo." + So: (-15, -s * a, 0), "antebrazo." + So: (0, -s * b, 0),  # noqa: E731
                               "mano." + So: (0, -s * b * 0.5, 0), "cabeza": (0, s * 8, 0), "columna": (0, s * 4, 0)}
        reposo = {"brazo." + So: (0, 0, 0), "antebrazo." + So: (0, 0, 0), "mano." + So: (0, 0, 0), "cabeza": (0, 0, 0), "columna": (0, 0, 0)}
        return 2.5, False, [
            (0.0, reposo, {}),
            (0.15, arriba(115, 20), {}),
            (0.30, arriba(130, -25), {}),
            (0.45, arriba(105, 30), {}),
            (0.60, arriba(130, -25), {}),
            (0.75, arriba(110, 25), {}),
            (1.0, reposo, {}),
        ]

    if accion == "saltar":
        rep = {k: (0, 0, 0) for k in ("muslo.L", "muslo.R", "pierna.L", "pierna.R", "pie.L", "pie.R", "brazo.L", "brazo.R", "columna", "cabeza")}
        def agachado(f):
            d = dict(rep)
            for L in ("L", "R"):
                d["muslo." + L] = (-50 * f, 0, 0)
                d["pierna." + L] = (80 * f, 0, 0)
                d["pie." + L] = (-30 * f, 0, 0)
                d["brazo." + L] = (35 * f, -lad(L) * 10 * f, 0)
            d["columna"] = (20 * f, 0, 0)
            d["cabeza"] = (-15 * f, 0, 0)
            return d
        aire = dict(rep)
        for L in ("L", "R"):
            aire["brazo." + L] = (-150, -lad(L) * 15, 0)
            aire["muslo." + L] = (-15, 0, 0)
            aire["pierna." + L] = (25, 0, 0)
            aire["pie." + L] = (20, 0, 0)
        return 1.4, False, [
            (0.0, rep, {"cadera": (0, 0, 0)}),
            (0.22, agachado(1.0), {"cadera": (0, 0, -0.07)}),
            (0.42, aire, {"cadera": (0, 0, 0.22)}),
            (0.55, aire, {"cadera": (0, 0, 0.26)}),
            (0.72, agachado(0.8), {"cadera": (0, 0, -0.06)}),
            (1.0, rep, {"cadera": (0, 0, 0)}),
        ]

    if accion == "bailar":
        def tiempo(g, brazo_arriba):
            d = {"cadera": (0, 0, g * 12), "columna": (0, g * 8, -g * 6), "cabeza": (0, -g * 10, 0),
                 "muslo.L": (-10 if g > 0 else 5, 0, 0), "muslo.R": (5 if g > 0 else -10, 0, 0),
                 "pierna.L": (25 if g > 0 else 0, 0, 0), "pierna.R": (0 if g > 0 else 25, 0, 0)}
            for L in ("L", "R"):
                if L == brazo_arriba:
                    d["brazo." + L] = (-20, -lad(L) * 150, 0)
                    d["antebrazo." + L] = (0, -lad(L) * 30, 0)
                else:
                    d["brazo." + L] = (-40, -lad(L) * 30, 0)
                    d["antebrazo." + L] = (-70, 0, 0)
            return d
        return 2.0, True, [
            (0.0, tiempo(1, "L"), {"cadera": (0.03, 0, -0.01)}),
            (0.25, tiempo(-1, "R"), {"cadera": (-0.03, 0, 0.01)}),
            (0.5, tiempo(1, "R"), {"cadera": (0.03, 0, -0.01)}),
            (0.75, tiempo(-1, "L"), {"cadera": (-0.03, 0, 0.01)}),
            (1.0, tiempo(1, "L"), {"cadera": (0.03, 0, -0.01)}),
        ]

    if accion in ("idle", "respirar"):
        a = {"pecho": (0, 0, 0), "cabeza": (0, 0, 0), "brazo.L": (0, 0, 0), "brazo.R": (0, 0, 0), "columna": (0, 0, 0)}
        b = {"pecho": (-3, 0, 0), "cabeza": (2, 0, 0), "brazo.L": (0, -3, 0), "brazo.R": (0, 3, 0), "columna": (-1, 0, 0)}
        return 3.0, True, [(0.0, a, {"cadera": (0, 0, 0)}), (0.45, b, {"cadera": (0, 0, 0.004)}), (1.0, a, {"cadera": (0, 0, 0)})]

    if accion == "asentir":
        k = lambda x: {"cabeza": (x, 0, 0), "cuello": (x * 0.3, 0, 0)}  # noqa: E731
        return 1.5, False, [(0.0, k(0), {}), (0.2, k(18), {}), (0.4, k(-6), {}), (0.6, k(15), {}), (0.8, k(-3), {}), (1.0, k(0), {})]

    if accion in ("girar_cabeza", "negar"):
        k = lambda zz: {"cabeza": (0, 0, zz), "cuello": (0, 0, zz * 0.3)}  # noqa: E731
        return 2.5, False, [(0.0, k(0), {}), (0.3, k(45), {}), (0.7, k(-45), {}), (1.0, k(0), {})]

    raise RuntimeError("acción desconocida: %s (andar, punetazos, saludar, saltar, bailar, idle, asentir, girar_cabeza, o --json)" % accion)


ACCIONES = ["andar", "punetazos", "saludar", "saltar", "bailar", "idle", "asentir", "girar_cabeza"]


def _canal(act, slot):
    from bpy_extras import anim_utils
    cb = anim_utils.action_get_channelbag_for_slot(act, slot)
    return list(cb.fcurves) if cb else []


def _cuat_local(pb, rot_deg, espacio):
    q = Euler([math.radians(a) for a in rot_deg], "XYZ").to_quaternion()
    if espacio == "local":
        return q
    B = pb.bone.matrix_local.to_quaternion()
    return B.inverted() @ q @ B


def _loc_local(pb, loc, espacio):
    v = Vector(loc)
    if espacio == "local":
        return v
    return pb.bone.matrix_local.to_quaternion().inverted() @ v


def animar(armature_nombre=None, accion=None, poses=None, segundos=None, fps=24, bucle=False, nla=False,
           lado="R", nombre=None, intensidad=1.0, inicio=None, pies=None):
    """Crea una Action con nombre en el esqueleto, a partir de una acción predefinida o de un JSON de poses."""
    _modo_objeto()
    sc = bpy.context.scene
    arm = armature(armature_nombre)
    H = float(arm.get("buho_altura", 1.0))
    rigidos = set(arm.get("buho_rigidos", []))
    huesos = arm.pose.bones
    sc.render.fps = int(fps)
    sc.render.fps_base = 1.0

    claves = []      # (frame, {hueso: quat}, {hueso: loc}, interp, easing)
    ignorados = set()
    espacio = "armature"
    if poses:
        with open(poses, encoding="utf-8") as f:
            datos = json.load(f)
        if isinstance(datos, list):
            datos = {"claves": datos}
        espacio = datos.get("espacio", "armature")
        fps = int(datos.get("fps", fps))
        sc.render.fps = fps
        bucle = bool(datos.get("bucle", bucle))
        nombre = nombre or datos.get("nombre") or os.path.splitext(os.path.basename(poses))[0]
        lista = sorted(datos["claves"], key=lambda c: c["frame"])
        f0 = lista[0]["frame"]
        base = int(inicio) if inicio is not None else int(f0)
        dur = lista[-1]["frame"] - f0
        for c in lista:
            rots, locs = {}, {}
            for hb, v in (c.get("huesos") or {}).items():
                if hb not in huesos:
                    ignorados.add(hb)
                    continue
                if "rot" in v:
                    rots[hb] = tuple(v["rot"])
                if "loc" in v:
                    locs[hb] = tuple(v["loc"])
            claves.append((base + c["frame"] - f0, rots, locs, c.get("interp", datos.get("interp", "BEZIER")),
                           c.get("easing", datos.get("easing", "AUTO"))))
        n_frames = dur
    else:
        if not accion:
            raise RuntimeError("falta la acción (" + ", ".join(ACCIONES) + ") o --json poses.json")
        base_s, natural, lista = _poses_predefinidas(accion, "L" if str(lado).upper().startswith("L") else "R")
        ciclos = 1
        if segundos and natural:
            ciclos = max(1, int(round(float(segundos) / base_s)))
        dur_s = float(segundos) if (segundos and not natural) else base_s * ciclos
        n_frames = max(2, int(round(dur_s * fps)))
        base = int(inicio) if inicio is not None else int(sc.frame_start)
        nombre = nombre or accion
        bucle = bucle or False
        for c in range(ciclos):
            for i, (t, rots, locs) in enumerate(lista):
                if c > 0 and i == 0:
                    continue
                tt = (c + t) / ciclos
                rr = {k: tuple(a * intensidad for a in v) for k, v in rots.items()}
                ll = {k: tuple(a * H * intensidad for a in v) for k, v in locs.items()}
                claves.append((base + int(round(tt * n_frames)), rr, ll, "BEZIER", "AUTO"))

    # Huesos que toca la acción: en todas las claves llevan valor (lo que falta = reposo), así no hay arrastres.
    rot_h = sorted(set(h for c in claves for h in c[1] if h in huesos))
    loc_h = sorted(set(h for c in claves for h in c[2] if h in huesos))
    for h in list(rot_h):
        if h in rigidos:
            rot_h.remove(h)
            ignorados.add(h + " (rígido)")
    for c in claves:
        for h in list(c[1]) + list(c[2]):
            if h not in huesos:
                ignorados.add(h)

    if not rot_h and not loc_h:
        raise RuntimeError("ningún hueso de la acción existe en %s (las acciones predefinidas son para el esqueleto "
                           "humanoide de buho-blender; para otros usa --json con sus nombres: %s)"
                           % (arm.name, ", ".join(b.name for b in arm.data.bones)[:300]))
    act = bpy.data.actions.new(nombre)
    arm.animation_data_create()
    previa = arm.animation_data.action
    arm.animation_data.action = act
    for pb in huesos:
        pb.rotation_mode = "QUATERNION"
    previas = {}
    for frame, rots, locs, interp, easing in claves:
        for h in rot_h:
            pb = huesos[h]
            q = _cuat_local(pb, rots.get(h, (0, 0, 0)), espacio)
            if h in previas and previas[h].dot(q) < 0:
                q.negate()
            previas[h] = q
            pb.rotation_quaternion = q
            pb.keyframe_insert("rotation_quaternion", frame=frame, group=h)
        for h in loc_h:
            pb = huesos[h]
            pb.location = _loc_local(pb, locs.get(h, (0, 0, 0)), espacio)
            pb.keyframe_insert("location", frame=frame, group=h)
    por_frame = {c[0]: (c[3], c[4]) for c in claves}
    slot = arm.animation_data.action_slot
    for fc in _canal(act, slot):
        for kp in fc.keyframe_points:
            interp, easing = por_frame.get(int(round(kp.co.x)), ("BEZIER", "AUTO"))
            kp.interpolation = interp
            kp.easing = easing
        if bucle:
            fc.modifiers.new("CYCLES")
        fc.update()
    act.use_fake_user = True
    ini, fin = min(c[0] for c in claves), max(c[0] for c in claves)
    act["buho_bucle"] = bool(bucle)

    resultado = {"accion": act.name, "esqueleto": arm.name, "fotogramas": [ini, fin], "fps": sc.render.fps,
                 "segundos": round((fin - ini) / sc.render.fps, 2), "huesos": rot_h, "con_desplazamiento": loc_h,
                 "bucle": bool(bucle), "ignorados": sorted(ignorados)}
    # Pies en el suelo (lib/buho_apoyo.py): por defecto apoyados; en saltos y poses a medida, solo sin atravesar.
    if pies is None:
        pies = "no_atravesar" if (poses or accion == "saltar") else "apoyar"
    import buho_apoyo
    resultado["pies"] = importlib.reload(buho_apoyo).apoyar(arm, ini, fin, pies)
    if nla:
        pistas = arm.animation_data.nla_tracks
        pista = pistas.get("buho") or pistas.new()
        pista.name = "buho"
        fin_prev = max([st.frame_end for st in pista.strips] or [None]) if pista.strips else None
        empieza = int(fin_prev) if fin_prev is not None and inicio is None else ini
        tira = pista.strips.new(act.name, empieza, act)
        try:
            tira.action_slot = slot
        except Exception:
            pass
        arm.animation_data.action = None
        if previa is not None and previa != act:
            arm.animation_data.action = None
        sc.frame_start = int(min(st.frame_start for st in pista.strips))
        sc.frame_end = int(max(st.frame_end for st in pista.strips)) - (1 if bucle else 0)
        resultado.update({"nla": {"pista": pista.name, "empieza": tira.frame_start, "acaba": tira.frame_end,
                                  "tiras": [st.name for st in pista.strips]}})
    else:
        sc.frame_start = ini
        sc.frame_end = fin - (1 if bucle else 0)
    sc.frame_set(sc.frame_start)
    resultado["escena"] = [sc.frame_start, sc.frame_end]
    return resultado


def reposo(armature_nombre=None):
    """Quita la acción activa y deja el esqueleto en su pose de reposo."""
    arm = armature(armature_nombre)
    if arm.animation_data:
        arm.animation_data.action = None
    for pb in arm.pose.bones:
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)
    return {"esqueleto": arm.name, "reposo": True}


# ======================================================================
# Animaciones de fuera (Mixamo y parecidos): importar y pasar al esqueleto propio
# ======================================================================

MAPA_MIXAMO = {
    "hips": "cadera", "spine": "columna", "spine2": "pecho", "neck": "cuello", "head": "cabeza",
    "leftshoulder": "hombro.L", "leftarm": "brazo.L", "leftforearm": "antebrazo.L", "lefthand": "mano.L",
    "rightshoulder": "hombro.R", "rightarm": "brazo.R", "rightforearm": "antebrazo.R", "righthand": "mano.R",
    "leftupleg": "muslo.L", "leftleg": "pierna.L", "leftfoot": "pie.L",
    "rightupleg": "muslo.R", "rightleg": "pierna.R", "rightfoot": "pie.R",
}


def _norm(n):
    return re.sub(r"[^a-z0-9.]", "", n.split(":")[-1].lower())


def importar_animacion(archivo, armature_nombre=None, nombre=None, en_sitio=False, conservar=False, nla=False,
                       pies="apoyar", cabeza="auto", estabilidad=None):
    """Importa un FBX con animación. Si hay esqueleto destino, pasa la animación a sus huesos por nombre
    (Mixamo -> nombres de buho_rig, o nombres iguales) y borra lo importado."""
    _modo_objeto()
    sc = bpy.context.scene
    if not os.path.exists(archivo):
        raise RuntimeError("no existe " + archivo)
    destino = None
    try:
        destino = armature(armature_nombre)
    except RuntimeError:
        if armature_nombre:
            raise
    antes_o, antes_a = set(bpy.data.objects), set(bpy.data.actions)
    bpy.ops.import_scene.fbx(filepath=archivo, automatic_bone_orientation=True)
    nuevos = [o for o in bpy.data.objects if o not in antes_o]
    nuevas_acc = [a for a in bpy.data.actions if a not in antes_a]
    src = next((o for o in nuevos if o.type == "ARMATURE"), None)
    if src is None:
        raise RuntimeError("el FBX no trae esqueleto")
    act_src = src.animation_data.action if src.animation_data else None
    if act_src is None:
        raise RuntimeError("el FBX no trae animación")
    f0, f1 = (int(round(v)) for v in act_src.frame_range)
    if destino is None:
        act_src.name = nombre or os.path.splitext(os.path.basename(archivo))[0]
        return {"importado": archivo, "esqueleto": src.name, "accion": act_src.name, "fotogramas": [f0, f1],
                "huesos": [b.name for b in src.data.bones], "retarget": False}

    # Parejas destino <- origen
    por_norm = {_norm(b.name): b.name for b in src.data.bones}
    tgt_norm = {_norm(b.name): b.name for b in destino.data.bones}
    pares = {}
    for nn, sb in por_norm.items():
        tb = MAPA_MIXAMO.get(nn) or tgt_norm.get(nn)
        if tb and tb in destino.data.bones:
            pares[tb] = sb
    if len(pares) < 5:
        raise RuntimeError("no encuentro huesos equivalentes (%d): el esqueleto del FBX tiene %s" % (len(pares), ", ".join(list(por_norm)[:12])))

    rigidos = set(destino.get("buho_rigidos", []))
    q_src_w = src.matrix_world.to_quaternion()
    q_tgt_w = destino.matrix_world.to_quaternion()
    rest_s = {sb: (src.matrix_world @ src.data.bones[sb].matrix_local).to_quaternion() for sb in pares.values()}
    rest_t = {b.name: b.matrix_local.to_quaternion() for b in destino.data.bones}
    corr = {}
    for tb, sb in pares.items():
        # En brazos y piernas se corrige la diferencia de pose de reposo (T de Mixamo frente a brazos caídos):
        # primero se lleva el hueso propio a la dirección del de origen y luego se aplica el giro del origen.
        # En columna, cuello, cabeza, manos y pies solo se copia el giro: sus huesos de origen pueden apuntar
        # a cualquier lado (p. ej. una cabeza sin hueso final apunta hacia delante).
        if tb.split(".")[0].split("_")[0] in ("brazo", "antebrazo", "muslo", "pierna"):
            dt = (q_tgt_w @ rest_t[tb]) @ Vector((0, 1, 0))
            ds = rest_s[sb] @ Vector((0, 1, 0))
            corr[tb] = dt.rotation_difference(ds)
        else:
            corr[tb] = Quaternion()
    # escala del desplazamiento de la cadera: alturas de la cadera en reposo
    raiz_s = pares.get("cadera")
    esc = 1.0
    if raiz_s:
        hs = (src.matrix_world @ src.data.bones[raiz_s].head_local).z
        ht = (destino.matrix_world @ destino.data.bones["cadera"].head_local).z - min(
            (destino.matrix_world @ b.head_local).z for b in destino.data.bones)
        hs_suelo = hs - min((src.matrix_world @ b.head_local).z for b in src.data.bones)
        esc = ht / hs_suelo if hs_suelo > 1e-6 else 1.0
        rest_raiz = src.matrix_world @ src.data.bones[raiz_s].head_local

    nombre = nombre or os.path.splitext(os.path.basename(archivo))[0]
    act = bpy.data.actions.new(nombre)
    destino.animation_data_create()
    destino.animation_data.action = act
    for pb in destino.pose.bones:
        pb.rotation_mode = "QUATERNION"
    orden = [b for b in destino.data.bones]  # bones ya va de padres a hijos
    previas = {}
    import buho_apoyo
    buho_apoyo = importlib.reload(buho_apoyo)
    estab = buho_apoyo.Estabilizador(destino, cabeza, estabilidad)
    alturas_src = {}
    for f in range(f0, f1 + 1):
        sc.frame_set(f)
        alturas_src[f] = buho_apoyo.altura_minima_huesos(src)
        pose_arm = {}
        for b in orden:
            P = pose_arm[b.parent.name] if b.parent else Quaternion()
            rel = (rest_t[b.parent.name].inverted() @ rest_t[b.name]) if b.parent else rest_t[b.name]
            sb = pares.get(b.name)
            if sb is None or b.name in rigidos:
                basis = Quaternion()
            else:
                q_sp = (src.matrix_world @ src.pose.bones[sb].matrix).to_quaternion()
                q_des_w = q_sp @ rest_s[sb].inverted() @ corr[b.name] @ (q_tgt_w @ rest_t[b.name])
                q_des = q_tgt_w.inverted() @ q_des_w
                basis = (P @ rel).inverted() @ q_des
                basis = estab.ajustar(b.name, basis, P, rel, rest_t.get("cabeza", Quaternion()))
            pose_arm[b.name] = P @ rel @ basis
            pb = destino.pose.bones[b.name]
            if b.name in previas and previas[b.name].dot(basis) < 0:
                basis.negate()
            previas[b.name] = basis
            if sb is not None and b.name not in rigidos:
                pb.rotation_quaternion = basis
                pb.keyframe_insert("rotation_quaternion", frame=f, group=b.name)
        if raiz_s:
            d = (src.matrix_world @ src.pose.bones[raiz_s].head) - rest_raiz
            if en_sitio:
                d.x = d.y = 0
            d_arm = q_tgt_w.inverted() @ (d * esc)
            pb = destino.pose.bones["cadera"]
            pb.location = rest_t["cadera"].inverted() @ d_arm
            pb.keyframe_insert("location", frame=f, group="cadera")
    act.use_fake_user = True
    sc.render.fps = int(round(sc.render.fps)) or 24
    sc.frame_start, sc.frame_end = f0, f1
    # Pies en el suelo: la altura sobre el suelo la marca el esqueleto de origen (así un salto sigue siendo salto).
    hs_src = (hs_suelo if raiz_s else 1.0)
    objetivo = buho_apoyo.objetivo_desde_origen(alturas_src, esc, 0.08 * hs_src)
    info_pies = buho_apoyo.apoyar(destino, f0, f1, pies, objetivo=objetivo, ignorar=nuevos) if pies not in (None, "no") else None

    if not conservar:
        for o in nuevos:
            data = o.data
            bpy.data.objects.remove(o, do_unlink=True)
            if data is not None and data.users == 0:
                for col in (bpy.data.meshes, bpy.data.armatures):
                    if data.name in col and col[data.name] == data:
                        col.remove(data)
        for a in nuevas_acc:
            if a.name in bpy.data.actions and a != act:
                bpy.data.actions.remove(a)
    resultado = {"importado": archivo, "esqueleto": destino.name, "accion": act.name, "fotogramas": [f0, f1],
                 "retarget": True, "parejas": pares, "sin_pareja": [b.name for b in destino.data.bones if b.name not in pares],
                 "escala_desplazamiento": round(esc, 4), "en_sitio": bool(en_sitio),
                 "pies": info_pies, **estab.info()}
    if nla:
        pista = destino.animation_data.nla_tracks.get("buho") or destino.animation_data.nla_tracks.new()
        pista.name = "buho"
        empieza = int(max([st.frame_end for st in pista.strips] or [f0]))
        tira = pista.strips.new(act.name, empieza, act)
        try:
            tira.action_slot = destino.animation_data.action_slot
        except Exception:
            pass
        destino.animation_data.action = None
        sc.frame_start = int(min(st.frame_start for st in pista.strips))
        sc.frame_end = int(max(st.frame_end for st in pista.strips))
        resultado["nla"] = {"pista": pista.name, "tiras": [st.name for st in pista.strips]}
    sc.frame_set(sc.frame_start)
    return resultado
