"""Logo de UCAM HiTech en 3D (uso personal de Julián, mentorías en UCAM HiTech).

Referencia: logo oficial de https://www.hitech.ucam.edu/ (en `referencia/`).
- El sello y la palabra «UCAM» se calcan del PNG oficial (marching squares sobre el canal alfa)
  y se extruyen como curvas 2D con bisel: así copian la forma real, no una fuente parecida.
- «HiTech» y la línea «SPORT, FOOD & HEALTH INNOVATION HUB» van con texto 3D en Segoe UI
  (el original usa Open Sans, que no está instalada; Segoe UI es casi igual).
- Colores del logo en color: azul UCAM #00457A, gris #3D3D3D; el sello va en azul sobre una
  placa blanca nacarada, como en el logo en color.

Idempotente: todo vive en la colección «UCAM_HiTech» y se rehace en cada pasada.
Animación: el pivote gira 360° en 8 s (192 fotogramas a 24 fps), en bucle perfecto.
"""
import math
import os

import bpy
import numpy as np

import buho3d as b3

AQUI = os.path.dirname(os.path.abspath(__file__))
PNG = os.path.join(AQUI, "referencia", "logo-hitech-horizontal-blanco.png")
FUENTES = r"C:\Windows\Fonts"

S = 0.01            # metros por píxel del PNG de referencia (740 x 168 px)
CX, CY = 370.0, 83.0  # centro del logo en píxeles (y hacia abajo)
ALTO_LOGO = 1.25    # altura del centro del logo sobre el suelo
FPS, SEGUNDOS = 24, 8

AZUL = "#00457A"
GRIS = "#3D3D3D"
GRIS_CLARO = "#5E5E5E"


# ----------------------------------------------------------------------
# Calco del PNG: alfa -> contornos
# ----------------------------------------------------------------------

def alfa_png(ruta):
    img = bpy.data.images.load(ruta, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)[:, :, 3], h  # fila 0 = abajo


def ampliar(a, k):
    h, w = a.shape
    ys = np.linspace(0, h - 1, (h - 1) * k + 1)
    xs = np.linspace(0, w - 1, (w - 1) * k + 1)
    y0 = np.clip(np.floor(ys).astype(int), 0, h - 2)
    x0 = np.clip(np.floor(xs).astype(int), 0, w - 2)
    wy = (ys - y0)[:, None]
    wx = (xs - x0)[None, :]
    a00 = a[y0][:, x0]
    a01 = a[y0][:, x0 + 1]
    a10 = a[y0 + 1][:, x0]
    a11 = a[y0 + 1][:, x0 + 1]
    return (1 - wy) * ((1 - wx) * a00 + wx * a01) + wy * ((1 - wx) * a10 + wx * a11)


def suavizar(a):
    k = np.array([1, 2, 1], dtype=np.float32) / 4
    a = np.apply_along_axis(lambda f: np.convolve(f, k, mode="same"), 0, a)
    return np.apply_along_axis(lambda f: np.convolve(f, k, mode="same"), 1, a)


# Segmentos por caso (bordes: 0 abajo, 1 derecha, 2 arriba, 3 izquierda)
_CASOS = {1: [(3, 0)], 2: [(0, 1)], 3: [(3, 1)], 4: [(1, 2)], 5: [(3, 0), (1, 2)], 6: [(0, 2)],
          7: [(3, 2)], 8: [(2, 3)], 9: [(0, 2)], 10: [(0, 1), (2, 3)], 11: [(1, 2)], 12: [(1, 3)],
          13: [(0, 1)], 14: [(0, 3)]}


def contornos(f, t=0.5):
    """Marching squares con interpolación: lista de bucles [(x, y), ...] en coords de la matriz."""
    f = np.pad(f, 1)
    a, b = f[:-1, :-1], f[:-1, 1:]
    c, d = f[1:, 1:], f[1:, :-1]
    caso = (a > t) * 1 + (b > t) * 2 + (c > t) * 4 + (d > t) * 8
    vecinos = {}
    pos = {}

    def punto(i, j, e):
        if e == 0:
            k = ("h", i, j)
        elif e == 1:
            k = ("v", i, j + 1)
        elif e == 2:
            k = ("h", i + 1, j)
        else:
            k = ("v", i, j)
        if k not in pos:
            _, ii, jj = k
            if k[0] == "h":
                f0, f1 = f[ii, jj], f[ii, jj + 1]
                pos[k] = (jj + (t - f0) / (f1 - f0) - 1, ii - 1)
            else:
                f0, f1 = f[ii, jj], f[ii + 1, jj]
                pos[k] = (jj - 1, ii + (t - f0) / (f1 - f0) - 1)
        return k

    for i, j in zip(*np.nonzero((caso > 0) & (caso < 15))):
        for e1, e2 in _CASOS[int(caso[i, j])]:
            p, q = punto(i, j, e1), punto(i, j, e2)
            vecinos.setdefault(p, []).append(q)
            vecinos.setdefault(q, []).append(p)

    bucles, visto = [], set()
    for inicio in vecinos:
        if inicio in visto:
            continue
        bucle, prev, cur = [], None, inicio
        while cur not in visto:
            visto.add(cur)
            bucle.append(pos[cur])
            sig = [n for n in vecinos[cur] if n != prev and n not in visto]
            if not sig:
                break
            prev, cur = cur, sig[0]
        if len(bucle) >= 4:
            bucles.append(bucle)
    return bucles


def area(b):
    x = np.array([p[0] for p in b])
    y = np.array([p[1] for p in b])
    return 0.5 * abs(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))


def calcar(alfa, h_img, x0, x1, ytop0, ytop1, k=4, t=0.5):
    """Recorta una zona del PNG (en px, y hacia abajo) y devuelve bucles en metros (X, Z)."""
    r0, r1 = h_img - 1 - ytop1, h_img - 1 - ytop0
    zona = alfa[r0:r1 + 1, x0:x1 + 1]
    campo = suavizar(ampliar(zona, k))
    salida = []
    for bucle in contornos(campo, t):
        if area(bucle) < (0.6 * k) ** 2:
            continue
        pts = [((x0 + x / k - CX) * S, (r0 + y / k - (h_img - 1 - CY)) * S) for x, y in bucle[::2]]
        salida.append(pts)
    return salida


# ----------------------------------------------------------------------
# Geometría
# ----------------------------------------------------------------------

def curva_2d(nombre, bucles, material, extrude, bisel, y=0.0, desplaz=0.0, col=None):
    cu = bpy.data.curves.new(nombre, "CURVE")
    cu.dimensions = "2D"
    cu.fill_mode = "BOTH"
    cu.extrude = extrude
    cu.bevel_depth = bisel
    cu.bevel_resolution = 3
    cu.offset = desplaz
    for pts in bucles:
        sp = cu.splines.new("POLY")
        sp.points.add(len(pts) - 1)
        sp.points.foreach_set("co", [v for x, z in pts for v in (x, z, 0.0, 1.0)])
        sp.use_cyclic_u = True
    o = bpy.data.objects.new(nombre, cu)
    o.data.materials.append(material)
    o.rotation_euler = (math.radians(90), 0, 0)
    o.location = (0, y, 0)
    (col or b3._col(None)).objects.link(o)
    return o


def texto(nombre, cuerpo, fuente, material, caja, extrude, bisel, y=0.0, fuente_negrita=None,
          negrita=None, material2=None, col=None, ancla="abajo"):
    """Texto 3D ajustado a una caja del PNG: caja = (x0, x1, ytop_ref) en px."""
    cu = bpy.data.curves.new(nombre, "FONT")
    cu.body = cuerpo
    cu.font = bpy.data.fonts.load(os.path.join(FUENTES, fuente), check_existing=True)
    if fuente_negrita:
        cu.font_bold = bpy.data.fonts.load(os.path.join(FUENTES, fuente_negrita), check_existing=True)
    cu.extrude = extrude
    cu.bevel_depth = bisel
    cu.bevel_resolution = 3
    cu.offset = -bisel * 0.5
    cu.resolution_u = 6
    o = bpy.data.objects.new(nombre, cu)
    o.data.materials.append(material)
    if material2:
        o.data.materials.append(material2)
    if negrita:
        a, b = negrita
        for i in range(a, b):
            cu.body_format[i].use_bold = True
            if material2:
                cu.body_format[i].material_index = 1
    (col or b3._col(None)).objects.link(o)
    bpy.context.view_layer.update()
    xs = [v[0] for v in o.bound_box]
    ys = [v[1] for v in o.bound_box]
    x0, x1, yref = caja
    esc = (x1 - x0) * S / (max(xs) - min(xs))
    o.scale = (esc, esc, 1.0)
    o.rotation_euler = (math.radians(90), 0, 0)
    xw = (x0 - CX) * S - min(xs) * esc
    if ancla == "abajo":   # línea base (la parte de abajo de las mayúsculas) en yref
        zw = (CY - yref) * S
    else:                  # parte de arriba en yref
        zw = (CY - yref) * S - max(ys) * esc
    o.location = (xw, y, zw)
    return o


def ciclorama(nombre, material, ancho=40.0, fondo=9.0, radio=3.5, alto=12.0, col=None):
    """Suelo + curva + pared, sin esquinas (fondo de estudio infinito)."""
    perfil = [(-14.0, 0.0), (fondo - radio, 0.0)]
    for k in range(1, 17):
        a = math.radians(90 * k / 16)
        perfil.append((fondo - radio + radio * math.sin(a), radio - radio * math.cos(a)))
    perfil.append((fondo, alto))
    import bmesh
    bm = bmesh.new()
    filas = []
    for x in (-ancho / 2, ancho / 2):
        filas.append([bm.verts.new((x, yy, zz)) for yy, zz in perfil])
    for i in range(len(perfil) - 1):
        bm.faces.new((filas[0][i], filas[1][i], filas[1][i + 1], filas[0][i + 1]))
    o = b3.objeto(nombre, bm, material, col=col)
    for p in o.data.polygons:
        p.use_smooth = True
    return o


# ----------------------------------------------------------------------
# Escena
# ----------------------------------------------------------------------

b3.quitar_por_defecto()
col = b3.coleccion("UCAM_HiTech")

m_azul = b3.pbr("UCAM_Azul", "#00325E", metal=0.25, rugosidad=0.3, capa=1.0)  # algo más oscuro que AZUL: la luz lo aclara
m_gris = b3.pbr("UCAM_Gris", GRIS, metal=0.85, rugosidad=0.28, capa=0.4)
m_gris2 = b3.pbr("UCAM_Gris_claro", GRIS_CLARO, metal=0.8, rugosidad=0.32)
m_placa = b3.pbr("UCAM_Placa_nacar", "#F3F2EE", metal=0.0, rugosidad=0.3, capa=0.6)
m_fondo = b3.pbr("UCAM_Fondo_estudio", "#DCDFE4", rugosidad=0.75)

pivote = bpy.data.objects.new("UCAM_Pivote", None)
col.objects.link(pivote)
pivote.empty_display_size = 1.0
pivote.location = (0, 0, ALTO_LOGO)

alfa, h_img = alfa_png(PNG)

# Sello: tinta azul en relieve sobre placa blanca
sello = calcar(alfa, h_img, 18, 138, 10, 156, k=5, t=0.38)
exterior = max(sello, key=area)
piezas = [
    curva_2d("Sello_Placa", [exterior], m_placa, 0.025, 0.004, y=0.02, desplaz=-0.012, col=col),
    curva_2d("Sello_Relieve", sello, m_azul, 0.03, 0.0, y=-0.03, col=col),
    # UCAM calcada del PNG (serifas reales del logo)
    curva_2d("UCAM", calcar(alfa, h_img, 152, 440, 24, 94), m_azul, 0.07, 0.009, y=-0.01,
             desplaz=-0.009, col=col),
    texto("HiTech", "HiTech", "segoeui.ttf", m_gris, (460, 716, 88), 0.05, 0.006, y=-0.01, col=col),
    texto("Lema", "SPORT, FOOD & HEALTH INNOVATION HUB", "segoeuisl.ttf", m_gris2, (157, 716, 114),
          0.03, 0.0025, y=-0.01, fuente_negrita="segoeuib.ttf", negrita=(21, 31), material2=m_gris,
          col=col, ancla="arriba"),
]
for o in piezas:
    o.parent = pivote

# Fondo, luces y cámara
ciclorama("Fondo_estudio", m_fondo, col=col)
b3.mundo("#E8EBEF", 0.55)
objetivo = (0, 0, ALTO_LOGO)
b3.luz("Clave", "AREA", 1400, (-5.5, -7.5, 6.5), objetivo, "#FFF1E0", 5.0, col)
b3.luz("Relleno", "AREA", 450, (7.0, -6.0, 2.5), objetivo, "#FFFFFF", 6.0, col)
b3.luz("Contra", "AREA", 1500, (1.5, 5.0, 6.0), objetivo, "#DCE6FF", 4.0, col)
b3.luz("Barrido", "AREA", 900, (0, -3.0, 7.5), objetivo, "#FFFFFF", 3.0, col)
cam = b3.camara("Camara", (0, -13.0, 2.0), (0, 0, ALTO_LOGO - 0.05), lente=50, col=col)

# Giro en bucle: 0° en el fotograma 1 y 360° en el 193 (fuera del rango, sin fotograma repetido)
sc = bpy.context.scene
sc.render.fps = FPS
sc.frame_start, sc.frame_end = 1, FPS * SEGUNDOS
pivote.animation_data_clear()
pivote.rotation_euler = (0, 0, 0)
pivote.keyframe_insert("rotation_euler", index=2, frame=1)
pivote.rotation_euler = (0, 0, 2 * math.pi)
pivote.keyframe_insert("rotation_euler", index=2, frame=FPS * SEGUNDOS + 1)
try:  # Blender 5: las curvas viven en capas/canales
    for capa in pivote.animation_data.action.layers:
        for tira in capa.strips:
            for bolsa in tira.channelbags:
                for fc in bolsa.fcurves:
                    for kp in fc.keyframe_points:
                        kp.interpolation = "LINEAR"
except Exception:
    pass
sc.frame_set(1)

b3.render("BLENDER_EEVEE", 1920, 1080, muestras=64, look="AgX - Medium High Contrast")

result = {
    "bucles_sello": len(sello),
    "objetos": [o.name for o in col.objects],
    "fotogramas": [sc.frame_start, sc.frame_end],
}
