# Monigote estilo «vinilo coleccionable» del señor Juan: cabeza grande, gafas de sol cuadradas negras,
# pelo corto oscuro con entradas, bigote y perilla canosos, camiseta negra de cuello redondo y vaqueros.
#   buho-blender run funko_juan.py [--fondo]   · idempotente. Mira hacia -Y. Alto total ~1 m.

import importlib
import math
import random
import sys

import bmesh
import bpy
from mathutils import Vector

LIB = r"C:\Users\jferc\Documents\Bussines\BUHO\repo auxiliar\BuhoAI-Blender-CLI\lib"
if LIB not in sys.path:
    sys.path.insert(0, LIB)
import buho3d as b3
importlib.reload(b3)

random.seed(3)
b3.quitar_por_defecto()
col = b3.coleccion("FunkoJuan")

# ---------------- materiales ----------------
def piel():
    m, n, b = b3.material_nodos("FJ_Piel")
    tc = n.nuevo("ShaderNodeTexCoord")
    sep = n.nuevo("ShaderNodeSeparateXYZ")
    n.unir(tc, "Object", sep, "Vector")
    # sombra de barba de pocos días en la mandíbula (solo en la cabeza: coordenadas locales)
    mand = n.rampa(n.mate("ADD", sep.outputs["Z"], 0.25), [(0.06, "#FFFFFF"), (0.15, "#000000")])   # solo z local < ~-0.15
    frente = n.rampa(sep.outputs["Y"], [(0.0, "#FFFFFF"), (0.2, "#000000")])
    puntos = n.nuevo("ShaderNodeTexNoise", Scale=220.0, Detail=2.0)
    n.unir(tc, "Object", puntos, "Vector")
    zona = n.mate("MULTIPLY", mand, n.mate("ADD", n.mate("MULTIPLY", frente, 0.4), 0.6))
    barba = n.mate("MULTIPLY", zona, n.rampa(puntos.outputs["Fac"], [(0.4, "#555555"), (0.62, "#FFFFFF")]))
    barba = n.mate("MULTIPLY", barba, n.nuevo("ShaderNodeValue").outputs[0])
    barba.node.inputs[1].default_value = 1.0
    # el nodo Value vale 0 por defecto: lo ponemos a 0.85 (intensidad de la sombra)
    for nd in n.nt.nodes:
        if nd.type == "VALUE":
            nd.outputs[0].default_value = 0.6
    color = n.mezcla(barba, "#E2B08C", "#4A3A33")
    n.nt.links.new(color, b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.42
    b.inputs["Subsurface Weight"].default_value = 0.15
    b.inputs["Subsurface Radius"].default_value = (0.5, 0.25, 0.15)
    b.inputs["Coat Weight"].default_value = 0.15
    return m


def pelo(nombre, base, gris=None, escala=260.0):
    m, n, b = b3.material_nodos(nombre)
    tc = n.nuevo("ShaderNodeTexCoord")
    r = n.nuevo("ShaderNodeTexNoise", Scale=escala, Detail=3.0)
    n.unir(tc, "Object", r, "Vector")
    col_ = n.mezcla(n.rampa(r.outputs["Fac"], [(0.45, "#000000"), (0.7, "#FFFFFF")]), base, gris or base)
    n.nt.links.new(col_, b.inputs["Base Color"])
    bump = n.nuevo("ShaderNodeBump", Strength=0.6, Distance=0.004)
    n.unir(r, "Fac", bump, "Height")
    n.unir(bump, "Normal", b, "Normal")
    b.inputs["Roughness"].default_value = 0.75
    b.inputs["Sheen Weight"].default_value = 0.3
    return m


def tela(nombre, base, punto=600.0):
    m, n, b = b3.material_nodos(nombre)
    tc = n.nuevo("ShaderNodeTexCoord")
    ondas = n.nuevo("ShaderNodeTexWave", wave_type="BANDS", bands_direction="X", Scale=punto, Distortion=0.4)
    n.unir(tc, "Object", ondas, "Vector")
    bump = n.nuevo("ShaderNodeBump", Strength=0.12, Distance=0.002)
    n.unir(ondas, "Fac", bump, "Height")
    n.unir(bump, "Normal", b, "Normal")
    b.inputs["Base Color"].default_value = b3.color(base)
    b.inputs["Roughness"].default_value = 0.85
    b.inputs["Sheen Weight"].default_value = 0.5
    b.inputs["Sheen Tint"].default_value = (0.6, 0.6, 0.65, 1)
    return m


PIEL = piel()
PIEL_LISA = b3.pbr("FJ_PielLisa", "#E2B08C", rugosidad=0.45, capa=0.15)
PELO = pelo("FJ_Pelo", "#17130F", "#2C2520")
BARBA = pelo("FJ_Barba", "#1F1A17", "#7A726B", escala=320.0)
CAMISETA = tela("FJ_Camiseta", "#0E0E10")
VAQUERO = tela("FJ_Vaquero", "#2B3D5E", punto=420.0)
MONTURA = b3.pbr("FJ_Montura", "#060606", rugosidad=0.12, capa=1.0)
VARILLA = b3.pbr("FJ_Varilla", "#1C2A1E", rugosidad=0.2, capa=0.8)   # verde muy oscuro, como en la foto
CRISTAL = b3.pbr("FJ_Cristal", "#0A0F0B", metal=0.3, rugosidad=0.04, capa=1.0)
ZAPATO = b3.pbr("FJ_Zapato", "#151515", rugosidad=0.5)
SUELA = b3.pbr("FJ_Suela", "#EDEDED", rugosidad=0.6)
PEANA = b3.pbr("FJ_Peana", "#0A1F3A", rugosidad=0.3, capa=0.5)
LABIO = b3.pbr("FJ_Labio", "#B9786A", rugosidad=0.5)

# ---------------- cabeza ----------------
HZ = 0.74          # centro de la cabeza
HW, HD, HH = 0.62, 0.54, 0.6


def forma_cabeza(p):
    # algo más ancha en las sienes, mandíbula redondeada y un poco más estrecha
    t = (p.z / HH) + 0.5
    p.x *= 0.9 + 0.12 * math.sin(min(t, 1) * math.pi * 0.9)
    if p.y < 0:
        p.y *= 0.92
    return p


cabeza = b3.objeto("Cabeza", b3.deformar(b3.bm_caja(HW, HD, HH, cortes=3), forma_cabeza), PIEL,
                   loc=(0, 0, HZ), bevel=0.0, subsurf=3)

# pelo: casquete corto con entradas (cortamos la copia de la cabeza por la línea del pelo)
bm = b3.deformar(b3.bm_caja(HW, HD, HH, cortes=3), forma_cabeza)
mesh_tmp = bpy.data.meshes.new("tmp")
bm.to_mesh(mesh_tmp)
bm.free()
tmp = bpy.data.objects.new("tmp", mesh_tmp)
col.objects.link(tmp)
md = tmp.modifiers.new("s", "SUBSURF")
md.levels = 3
bpy.context.view_layer.update()
bm = bmesh.new()
bm.from_mesh(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh())
bpy.data.objects.remove(tmp, do_unlink=True)
bpy.data.meshes.remove(mesh_tmp)


def _en_pelo_viejo(v):
    x, y, z = v.co
    linea = 0.13 + 0.06 * max(0.0, -y / (HD / 2)) - 0.05 * (abs(x) / (HW / 2)) ** 2   # entradas: más alto delante
    if y > 0:
        linea = -0.05 - 0.12 * (y / (HD / 2))                                      # por detrás baja hasta la nuca
    if abs(x) > HW * 0.42 and y > -0.08 and z < 0.1:
        return z > 0.02 and y > 0.0                                                 # patillas cortas, sin tapar orejas
    return z > linea


# línea del pelo: corte inclinado (alto delante por las entradas, bajo en la nuca)
bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 0, 0.03),
                       plane_no=Vector((0, 0.65, 1)).normalized(), clear_inner=True)
bm.normal_update()
for v in bm.verts:
    v.co += v.normal * 0.012
bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=0.018)
b3.objeto("Pelo", bm, PELO, loc=(0, 0, HZ))

# orejas
for s in (-1, 1):
    bm = b3.deformar(b3.bm_esfera(0.07, 24, 12), lambda p: Vector((p.x * 0.45, p.y * 0.8, p.z * 1.15)))
    b3.objeto(f"Oreja_{'D' if s > 0 else 'I'}", bm, PIEL_LISA, loc=(s * 0.325, 0.03, HZ - 0.01), rot=(0, 0, s * -18), subsurf=1)

# nariz pequeña
b3.objeto("Nariz", b3.deformar(b3.bm_esfera(0.045, 24, 12), lambda p: Vector((p.x, p.y * 0.8, p.z * 1.2))), PIEL_LISA,
          loc=(0, -0.262, HZ - 0.06))

# cejas gruesas por encima de las gafas
for s in (-1, 1):
    b3.tubo(f"Ceja_{'D' if s > 0 else 'I'}", [(s * 0.05, -0.27, HZ + 0.125), (s * 0.13, -0.266, HZ + 0.14), (s * 0.22, -0.245, HZ + 0.13)],
            0.016, PELO, radios=[0.9, 1.0, 0.6])

# bigote, perilla y boca con media sonrisa
b3.tubo("Bigote", [(-0.1, -0.245, HZ - 0.165), (-0.05, -0.262, HZ - 0.125), (0, -0.266, HZ - 0.12),
                   (0.05, -0.262, HZ - 0.125), (0.1, -0.245, HZ - 0.165)], 0.024, BARBA, radios=[0.5, 1.0, 0.9, 1.0, 0.5])
b3.tubo("Labio", [(-0.055, -0.257, HZ - 0.16), (0, -0.262, HZ - 0.165), (0.06, -0.255, HZ - 0.152)], 0.009, LABIO)
bm = b3.deformar(b3.bm_esfera(0.11, 32, 16), lambda p: Vector((p.x * 0.95, p.y * 0.42, p.z * (1.25 if p.z < 0 else 0.8))))
b3.objeto("Perilla", bm, BARBA, loc=(0, -0.24, HZ - 0.235), subsurf=1)

# ---------------- gafas de sol cuadradas ----------------
GY = -0.285
GZ = HZ + 0.025
gafas = []
for s in (-1, 1):
    lado = "D" if s > 0 else "I"
    cx = s * 0.135
    marco = b3.objeto(f"Marco_{lado}", b3.bm_caja(0.215, 0.035, 0.165, cortes=1), MONTURA, loc=(cx, GY, GZ), bevel=0.02)
    hueco = b3.objeto(f"Hueco_{lado}", b3.bm_caja(0.165, 0.1, 0.12), MONTURA, loc=(cx, GY, GZ - 0.004), bevel=0.025)
    b3.booleano(marco, hueco)
    b3.objeto(f"Cristal_{lado}", b3.bm_caja(0.175, 0.008, 0.13), CRISTAL, loc=(cx, GY + 0.004, GZ - 0.004), bevel=0.028)
    # varilla hasta la oreja
    b3.objeto(f"Varilla_{lado}", b3.bm_caja(0.016, 0.3, 0.022), VARILLA, loc=(s * 0.305, GY + 0.15, GZ + 0.04),
              rot=(0, 0, s * -5), bevel=0.006)
    b3.objeto(f"Bisagra_{lado}", b3.bm_caja(0.04, 0.04, 0.03), MONTURA, loc=(s * 0.25, GY + 0.01, GZ + 0.045), bevel=0.008)
b3.objeto("Puente", b3.bm_caja(0.07, 0.025, 0.025), MONTURA, loc=(0, GY, GZ + 0.045), bevel=0.008)

# ---------------- cuerpo ----------------
b3.cilindro("Cuello", 0.075, 0.08, PIEL_LISA, loc=(0, 0.01, 0.44))
torso = b3.objeto("Torso", b3.deformar(b3.bm_caja(0.4, 0.26, 0.3, cortes=2),
                                       lambda p: Vector((p.x * (1 - 0.12 * (p.z < 0)), p.y, p.z))), CAMISETA,
                  loc=(0, 0.01, 0.29), subsurf=2)
b3.objeto("CuelloCamiseta", b3.bm_toro(0.088, 0.016, 48, 12), CAMISETA, loc=(0, 0.0, 0.435), escala=(1, 0.9, 1))
for s in (-1, 1):
    lado = "D" if s > 0 else "I"
    b3.objeto(f"Manga_{lado}", b3.bm_cilindro(0.068, 0.062, 0.12), CAMISETA, loc=(s * 0.215, 0.01, 0.37),
              rot=(0, s * 25, 0), bevel=0.01, subsurf=1)
    b3.tubo(f"Brazo_{lado}", [(s * 0.24, 0.01, 0.33), (s * 0.265, 0.0, 0.24), (s * 0.27, -0.02, 0.17)], 0.045, PIEL_LISA)
    b3.objeto(f"Mano_{lado}", b3.deformar(b3.bm_esfera(0.055, 24, 12), lambda p: Vector((p.x * 0.8, p.y, p.z * 1.1))),
              PIEL_LISA, loc=(s * 0.27, -0.025, 0.14), subsurf=1)
    b3.objeto(f"Pierna_{lado}", b3.bm_cilindro(0.075, 0.07, 0.12), VAQUERO, loc=(s * 0.09, 0.01, 0.11), bevel=0.02, subsurf=1)
    b3.objeto(f"Zapato_{lado}", b3.deformar(b3.bm_esfera(0.08, 32, 16), lambda p: Vector((p.x * 0.95, p.y * 1.35, p.z * 0.6))),
              ZAPATO, loc=(s * 0.09, -0.025, 0.045), subsurf=1)
    b3.objeto(f"Suela_{lado}", b3.deformar(b3.bm_cilindro(0.08, 0.08, 0.02, 32), lambda p: Vector((p.x * 0.95, p.y * 1.35, p.z))),
              SUELA, loc=(s * 0.09, -0.025, 0.012), bevel=0.006)
b3.objeto("Peana", b3.bm_revolucion([(0.36, -0.05), (0.37, -0.03), (0.36, 0.0), (0.0, 0.0)], 96), PEANA, loc=(0, 0, 0))

# ---------------- escena ----------------
b3.camara("FJ_Cam", (1.05, -2.35, 0.95), (0, 0, 0.55), lente=50)
b3.estudio((0, 0, 0.55), distancia=3.0, fuerza=1.1)
b3.mundo("#C9CFD6", 0.6)
b3.render("BLENDER_EEVEE", 1600, 1600, muestras=64, look="AgX - Medium High Contrast")
b3.resplandor(0.2, 1.5, 5)

result = {"objetos": len(col.all_objects)}
