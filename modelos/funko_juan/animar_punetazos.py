# Anima al señor Juan dando puñetazos (jab derecho, jab izquierdo, directo y gancho) en bucle.
# Se ejecuta sobre funko_juan.blend (después de funko_juan.py). Idempotente.
# Pivotes: «Cintura» mueve todo el cuerpo de arriba; «Hombro_D/I» mueven manga, brazo y mano.

import math

import bpy
from mathutils import Vector

sc = bpy.context.scene
col = bpy.data.collections["FunkoJuan"]
FPS = 24
sc.render.fps = FPS


def vacio(nombre, pos):
    o = bpy.data.objects.get(nombre)
    if o:
        bpy.data.objects.remove(o, do_unlink=True)
    o = bpy.data.objects.new(nombre, None)
    o.empty_display_type = "SPHERE"
    o.empty_display_size = 0.05
    o.location = pos
    col.objects.link(o)
    return o


def colgar(hijo, padre):
    bpy.context.view_layer.update()
    mw = hijo.matrix_world.copy()
    hijo.parent = padre
    hijo.matrix_parent_inverse = padre.matrix_world.inverted()
    hijo.matrix_world = mw


# Quita animación previa
for o in col.all_objects:
    o.animation_data_clear()
    if o.parent and o.parent.name in ("Cintura", "Hombro_D", "Hombro_I"):
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw

cintura = vacio("Cintura", (0, 0.01, 0.2))
hombros = {s: vacio(f"Hombro_{'D' if s > 0 else 'I'}", (s * 0.215, 0.01, 0.37)) for s in (-1, 1)}
bpy.context.view_layer.update()

abajo = ("Pierna", "Zapato", "Suela", "Peana", "FJ_Cam", "Clave", "Relleno", "Contra", "Cintura", "Hombro")
for o in list(col.all_objects):
    if o.name.startswith(abajo) or o.type in {"CAMERA", "LIGHT"}:
        continue
    lado = 1 if o.name.endswith("_D") else -1 if o.name.endswith("_I") else 0
    if lado and o.name.startswith(("Manga", "Brazo", "Mano")):
        colgar(o, hombros[lado])
    else:
        colgar(o, cintura)
for h in hombros.values():
    colgar(h, cintura)

# ---------------- poses ----------------
GUARDIA = (-55, 0, 0)          # brazos arriba delante de la cara (grados X, Y, Z)


def pose(f, rot_cintura_z=0.0, salto=0.0, d=GUARDIA, i=GUARDIA):
    sc.frame_set(f)
    cintura.rotation_euler = (0, 0, math.radians(rot_cintura_z))
    cintura.location = (0, 0.01, 0.2 + salto)
    cintura.keyframe_insert("rotation_euler", frame=f)
    cintura.keyframe_insert("location", frame=f)
    for s, r in ((1, d), (-1, i)):
        h = hombros[s]
        h.rotation_euler = tuple(math.radians(a) for a in r)
        h.keyframe_insert("rotation_euler", frame=f)


# brazo estirado hacia delante: -88° en X; el giro en Z lo cruza hacia el centro
JAB_D = (-90, 0, 12)
JAB_I = (-90, 0, -12)
GANCHO_I = (-80, -55, -40)     # codo abierto, sale de lado
CARGA_D = (-35, 0, -8)

ciclo = [
    # f,  cintura Z, salto, brazo D, brazo I
    (1, 0, 0.0, GUARDIA, GUARDIA),
    (5, -14, 0.01, JAB_D, GUARDIA),      # jab derecho
    (10, 0, 0.0, GUARDIA, GUARDIA),
    (14, 14, 0.01, GUARDIA, JAB_I),      # jab izquierdo
    (19, 0, 0.0, GUARDIA, GUARDIA),
    (22, 6, 0.0, CARGA_D, GUARDIA),      # carga
    (26, -22, 0.02, JAB_D, GUARDIA),     # directo fuerte
    (32, 0, 0.0, GUARDIA, GUARDIA),
    (37, 26, 0.015, GUARDIA, GANCHO_I),  # gancho izquierdo
    (44, 0, 0.0, GUARDIA, GUARDIA),
    (48, 0, 0.0, GUARDIA, GUARDIA),
]
VUELTAS = globals().get("VUELTAS_PUNOS", 2)
LARGO = 48
for v in range(VUELTAS):
    for f, rz, salto, d, i in ciclo:
        pose(f + v * LARGO, rz, salto, d, i)

# golpes secos: salida rápida y vuelta suave
for o in (cintura, *hombros.values()):
    ad = o.animation_data
    if ad and ad.action:
        for fc in getattr(ad.action, "fcurves", []):
            for k in fc.keyframe_points:
                k.interpolation = "BEZIER"
                k.easing = "EASE_OUT"

sc.frame_start = 1
sc.frame_end = LARGO * VUELTAS
sc.frame_set(1)

result = {"fotogramas": sc.frame_end, "fps": FPS, "segundos": sc.frame_end / FPS}
