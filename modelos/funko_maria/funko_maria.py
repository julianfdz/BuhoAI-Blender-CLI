# Figura estilo vinilo coleccionable de María (joven): pelo largo liso castaño claro con raya, camiseta blanca,
# mochila del insti con asas estampadas, auriculares de cable y la mano en «cuernos» rockeros junto a la cara.
#   buho-blender run funko_maria.py [--fondo]   · idempotente. Mira hacia -Y. Alto total ~1 m.

import importlib
import math
import sys

import bmesh
import bpy
from mathutils import Vector

LIB = r"C:\Users\jferc\Documents\Bussines\BUHO\repo auxiliar\BuhoAI-Blender-CLI\lib"
if LIB not in sys.path:
    sys.path.insert(0, LIB)
import buho3d as b3
importlib.reload(b3)

b3.quitar_por_defecto()
col = b3.coleccion("FunkoMaria")
cabeza_objs = []


def de_cabeza(o):
    cabeza_objs.append(o)
    return o


# ---------------- materiales ----------------
def pelo_mat():
    m, n, b = b3.material_nodos("FM_Pelo")
    tc = n.nuevo("ShaderNodeTexCoord")
    sep = n.nuevo("ShaderNodeSeparateXYZ")
    n.unir(tc, "Object", sep, "Vector")
    # raíz más oscura, puntas más claras (z local del objeto)
    grad = n.rampa(sep.outputs["Z"], [(0.0, "#B9926A"), (0.45, "#9A7550"), (0.75, "#6E4F36")])
    mechones = n.nuevo("ShaderNodeTexWave", wave_type="BANDS", bands_direction="X", Scale=140.0, Distortion=1.2, Detail=3.0)
    n.unir(tc, "Object", mechones, "Vector")
    col_ = n.mezcla(n.mate("MULTIPLY", mechones.outputs["Fac"], 0.35), grad, "#3E2A1C", "MULTIPLY")
    n.nt.links.new(col_, b.inputs["Base Color"])
    bump = n.nuevo("ShaderNodeBump", Strength=0.35, Distance=0.003)
    n.unir(mechones, "Fac", bump, "Height")
    n.unir(bump, "Normal", b, "Normal")
    b.inputs["Roughness"].default_value = 0.42
    b.inputs["Sheen Weight"].default_value = 0.4
    b.inputs["Coat Weight"].default_value = 0.2
    return m


def tela(nombre, base, punto=600.0):
    m, n, b = b3.material_nodos(nombre)
    tc = n.nuevo("ShaderNodeTexCoord")
    ondas = n.nuevo("ShaderNodeTexWave", wave_type="BANDS", bands_direction="X", Scale=punto, Distortion=0.4)
    n.unir(tc, "Object", ondas, "Vector")
    bump = n.nuevo("ShaderNodeBump", Strength=0.1, Distance=0.002)
    n.unir(ondas, "Fac", bump, "Height")
    n.unir(bump, "Normal", b, "Normal")
    b.inputs["Base Color"].default_value = b3.color(base)
    b.inputs["Roughness"].default_value = 0.85
    b.inputs["Sheen Weight"].default_value = 0.4
    return m


def asa_mat():
    """Cinta negra con un estampado geométrico blanco (rombos), como el asa de la mochila de la foto."""
    m, n, b = b3.material_nodos("FM_Asa")
    tc = n.nuevo("ShaderNodeTexCoord")
    sep = n.nuevo("ShaderNodeSeparateXYZ")
    n.unir(tc, "Generated", sep, "Vector")
    # u (largo de la curva) y v (vuelta al tubo): rombos = |frac(u*k)-0.5| + |frac(v*2)-0.5| < 0.35
    a = n.mate("ABSOLUTE", n.mate("SUBTRACT", n.mate("FRACT", n.mate("MULTIPLY", sep.outputs["X"], 26.0)), 0.5))
    c = n.mate("ABSOLUTE", n.mate("SUBTRACT", n.mate("FRACT", n.mate("MULTIPLY", sep.outputs["Y"], 2.0)), 0.5))
    rombo = n.mate("LESS_THAN", n.mate("ADD", a, c), 0.42)
    borde = n.mate("GREATER_THAN", n.mate("ADD", a, c), 0.32)
    patron = n.mate("MULTIPLY", rombo, borde)
    n.nt.links.new(n.mezcla(patron, "#111111", "#EDEBE6"), b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.8
    return m


PIEL = b3.pbr("FM_Piel", "#F0C7A6", rugosidad=0.45, capa=0.15)
PELO = pelo_mat()
CAMISETA = tela("FM_Camiseta", "#F4F3EF")
VAQUERO = tela("FM_Vaquero", "#6F8DB5", punto=420.0)
MOCHILA = tela("FM_Mochila", "#18181B", punto=300.0)
ASA = asa_mat()
OJO = b3.pbr("FM_Ojo", "#050505", rugosidad=0.08, capa=1.0)
BRILLO = b3.emisivo("FM_Brillo", "#FFFFFF", 1.5)
CEJA = b3.pbr("FM_Ceja", "#6A4A33", rugosidad=0.7)
LABIOS = b3.pbr("FM_Labios", "#D98C86", rugosidad=0.4, capa=0.3)
UNAS = b3.pbr("FM_Unas", "#8E0F1E", rugosidad=0.15, capa=1.0)
AURICULAR = b3.pbr("FM_Auricular", "#F7F7F7", rugosidad=0.3, capa=0.5)
ZAPATILLA = b3.pbr("FM_Zapatilla", "#F2F2F2", rugosidad=0.5)
SUELA = b3.pbr("FM_Suela", "#D9D4CC", rugosidad=0.6)
PEANA = b3.pbr("FM_Peana", "#0A1F3A", rugosidad=0.3, capa=0.5)

# ---------------- cabeza ----------------
HZ = 0.74
HW, HD, HH = 0.62, 0.54, 0.6


def forma_cabeza(p):
    t = (p.z / HH) + 0.5
    p.x *= 0.88 + 0.12 * math.sin(min(t, 1) * math.pi * 0.9)
    if p.y < 0:
        p.y *= 0.92
    return p


de_cabeza(b3.objeto("Cabeza", b3.deformar(b3.bm_caja(HW, HD, HH, cortes=3), forma_cabeza), PIEL, loc=(0, 0, HZ), subsurf=3))

# casquete de pelo: copia suavizada de la cabeza con la frente libre (flequillo apartado, raya al medio)
bm = b3.deformar(b3.bm_caja(HW, HD, HH, cortes=3), forma_cabeza)
me = bpy.data.meshes.new("tmp")
bm.to_mesh(me)
bm.free()
tmp = bpy.data.objects.new("tmp", me)
col.objects.link(tmp)
tmp.modifiers.new("s", "SUBSURF").levels = 3
bpy.context.view_layer.update()
bm = bmesh.new()
bm.from_mesh(tmp.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh())
bpy.data.objects.remove(tmp, do_unlink=True)
bpy.data.meshes.remove(me)
bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 0, -0.26),
                       plane_no=Vector((0, 1.6, 1)).normalized(), clear_inner=True)
bm.normal_update()
for v in bm.verts:
    v.co += v.normal * 0.016
bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=0.02)
de_cabeza(b3.objeto("PeloCasquete", bm, PELO, loc=(0, 0, HZ)))
# raya en medio: surco fino
de_cabeza(b3.tubo("Raya", [(0, -0.24, HZ + 0.25), (0, -0.12, HZ + 0.325), (0, 0.05, HZ + 0.33)], 0.006, CEJA))

# melena: dos cortinas delante de los hombros y un manto detrás
for s in (-1, 1):
    def cortina(u, v, s=s):
        ancho = 0.11 + 0.03 * u
        x = s * (0.285 + 0.03 * math.sin(v * math.pi) - 0.05 * u)
        y = -0.19 + v * ancho * 2 - 0.02 * u
        z = HZ + 0.12 - 0.62 * u
        # puntas un poco desfiladas
        if u > 0.9:
            z += 0.03 * math.sin(v * math.pi * 5)
        return (x, y, z)
    de_cabeza(b3.objeto(f"Melena_{'D' if s > 0 else 'I'}", b3.bm_rejilla(16, 8, cortina), PELO, solidify=0.04, subsurf=2))


def manto(u, v):
    x = (v - 0.5) * 0.62
    y = 0.27 + 0.03 * math.cos((v - 0.5) * math.pi) - 0.04 * u
    z = HZ + 0.1 - 0.55 * u
    if u > 0.9:
        z += 0.03 * math.sin(v * math.pi * 7)
    return (x, y, z)


de_cabeza(b3.objeto("MelenaAtras", b3.bm_rejilla(14, 16, manto), PELO, solidify=0.05, subsurf=1))

# cara: ojos de vinilo, cejas finas, nariz pequeña y labios
for s in (-1, 1):
    lado = "D" if s > 0 else "I"
    de_cabeza(b3.objeto(f"Ojo_{lado}", b3.deformar(b3.bm_esfera(0.052, 32, 16), lambda p: Vector((p.x * 0.85, p.y * 0.4, p.z * 1.1))),
                        OJO, loc=(s * 0.125, -0.262, HZ - 0.03)))
    de_cabeza(b3.objeto(f"Brillo_{lado}", b3.bm_esfera(0.012, 16, 8), BRILLO, loc=(s * 0.125 - 0.018, -0.282, HZ - 0.005)))
    de_cabeza(b3.tubo(f"Ceja_{lado}", [(s * 0.07, -0.268, HZ + 0.06), (s * 0.13, -0.27, HZ + 0.075), (s * 0.19, -0.262, HZ + 0.065)],
                      0.008, CEJA, radios=[0.6, 1.0, 0.5]))
de_cabeza(b3.objeto("Nariz", b3.deformar(b3.bm_esfera(0.03, 24, 12), lambda p: Vector((p.x, p.y * 0.7, p.z * 1.1))), PIEL,
                    loc=(0, -0.268, HZ - 0.09)))
de_cabeza(b3.tubo("Labios", [(-0.04, -0.262, HZ - 0.165), (0, -0.27, HZ - 0.172), (0.04, -0.262, HZ - 0.165)], 0.012, LABIOS,
                  radios=[0.5, 1.0, 0.5]))

# auriculares de cable: botón blanco en cada oreja y cable que baja al pecho
for s in (-1, 1):
    de_cabeza(b3.objeto(f"Auricular_{'D' if s > 0 else 'I'}", b3.bm_esfera(0.022, 16, 8), AURICULAR, loc=(s * 0.27, -0.13, HZ - 0.06)))
    b3.tubo(f"Cable_{'D' if s > 0 else 'I'}", [(s * 0.27, -0.14, HZ - 0.07), (s * 0.22, -0.2, HZ - 0.25), (s * 0.08, -0.15, 0.41),
                                                 (0, -0.14, 0.36)], 0.0035, AURICULAR)
b3.tubo("CableUnido", [(0, -0.14, 0.36), (0.01, -0.135, 0.3), (0.03, -0.13, 0.22)], 0.004, AURICULAR)

# ---------------- cuerpo ----------------
b3.cilindro("Cuello", 0.065, 0.08, PIEL, loc=(0, 0.01, 0.44))
b3.objeto("Torso", b3.deformar(b3.bm_caja(0.36, 0.24, 0.3, cortes=2), lambda p: Vector((p.x * (1 - 0.1 * (p.z < 0)), p.y, p.z))),
          CAMISETA, loc=(0, 0.01, 0.29), subsurf=2)
b3.objeto("CuelloCamiseta", b3.bm_toro(0.08, 0.012, 48, 12), CAMISETA, loc=(0, 0.0, 0.437), escala=(1, 0.9, 1))
for s in (-1, 1):
    b3.objeto(f"Pierna_{'D' if s > 0 else 'I'}", b3.bm_cilindro(0.07, 0.065, 0.12), VAQUERO, loc=(s * 0.085, 0.01, 0.11), bevel=0.02, subsurf=1)
    b3.objeto(f"Zapatilla_{'D' if s > 0 else 'I'}", b3.deformar(b3.bm_esfera(0.075, 32, 16), lambda p: Vector((p.x * 0.95, p.y * 1.35, p.z * 0.6))),
              ZAPATILLA, loc=(s * 0.085, -0.025, 0.045), subsurf=1)
    b3.objeto(f"Suela_{'D' if s > 0 else 'I'}", b3.deformar(b3.bm_cilindro(0.075, 0.075, 0.02, 32), lambda p: Vector((p.x * 0.95, p.y * 1.35, p.z))),
              SUELA, loc=(s * 0.085, -0.025, 0.012), bevel=0.006)

# brazo izquierdo (+X): baja y sujeta el asa de la mochila
b3.objeto("Manga_I", b3.bm_cilindro(0.062, 0.058, 0.11), CAMISETA, loc=(0.2, 0.01, 0.37), rot=(0, 25, 0), bevel=0.01, subsurf=1)
b3.tubo("Brazo_I", [(0.22, 0.01, 0.33), (0.23, -0.04, 0.25), (0.15, -0.12, 0.25)], 0.04, PIEL)
b3.objeto("Mano_I", b3.deformar(b3.bm_esfera(0.05, 24, 12), lambda p: Vector((p.x * 0.8, p.y, p.z * 1.1))), PIEL,
          loc=(0.13, -0.14, 0.26), subsurf=1)

# brazo derecho (-X): levantado, mano en cuernos junto a la cara
b3.objeto("Manga_D", b3.bm_cilindro(0.062, 0.058, 0.11), CAMISETA, loc=(-0.21, 0.0, 0.38), rot=(0, -70, 0), bevel=0.01, subsurf=1)
b3.tubo("Brazo_D", [(-0.24, 0.0, 0.38), (-0.36, -0.05, 0.40), (-0.42, -0.08, 0.52)], 0.04, PIEL)
H = Vector((-0.42, -0.09, 0.575))                                   # centro de la palma, mirando al frente
b3.objeto("Palma_D", b3.deformar(b3.bm_esfera(0.05, 24, 12), lambda p: Vector((p.x * 0.95, p.y * 0.55, p.z * 1.05))), PIEL,
          loc=H, subsurf=1)


def dedo(nombre, base, punta, r=0.014, una=True):
    b3.tubo(nombre, [base, (base + punta) / 2, punta], r, PIEL)
    if una:
        d = (punta - base).normalized()
        b3.objeto(nombre + "_Una", b3.deformar(b3.bm_esfera(r * 0.9, 16, 8), lambda p: Vector((p.x, p.y * 0.4, p.z * 1.3))), UNAS,
                  loc=punta - d * r * 0.6 + Vector((0, -r * 0.75, 0)))


dedo("Indice_D", H + Vector((0.028, -0.005, 0.04)), H + Vector((0.034, -0.008, 0.11)))      # índice arriba
dedo("Menique_D", H + Vector((-0.03, -0.005, 0.03)), H + Vector((-0.045, -0.008, 0.088)), r=0.012)  # meñique arriba
for i, dx in enumerate((0.012, -0.012)):                              # corazón y anular doblados
    b3.objeto(f"Doblado_D{i}", b3.bm_esfera(0.017, 16, 8), PIEL, loc=H + Vector((dx, -0.03, 0.035)))
dedo("Pulgar_D", H + Vector((0.035, -0.02, -0.01)), H + Vector((0.0, -0.045, 0.02)), r=0.014, una=False)

# mochila y asas
b3.objeto("Mochila", b3.bm_caja(0.32, 0.15, 0.3, cortes=2), MOCHILA, loc=(0, 0.2, 0.3), subsurf=2)
b3.objeto("Bolsillo", b3.bm_caja(0.22, 0.06, 0.13, cortes=1), MOCHILA, loc=(0, 0.28, 0.24), subsurf=2)
b3.objeto("FranjaMochila", b3.bm_caja(0.326, 0.155, 0.04), ASA, loc=(0, 0.2, 0.36), bevel=0.01)
for s in (-1, 1):
    b3.tubo(f"Asa_{'D' if s > 0 else 'I'}", [(s * 0.11, 0.16, 0.43), (s * 0.12, 0.02, 0.462), (s * 0.12, -0.13, 0.4),
                                            (s * 0.115, -0.135, 0.22), (s * 0.11, 0.1, 0.17)], 0.014, ASA)

# inclinación de cabeza como en la foto
piv = bpy.data.objects.new("PivoteCabeza", None)
piv.location = (0, 0, 0.46)
col.objects.link(piv)
bpy.context.view_layer.update()
for o in cabeza_objs:
    mw = o.matrix_world.copy()
    o.parent = piv
    o.matrix_parent_inverse = piv.matrix_world.inverted()
    o.matrix_world = mw
piv.rotation_euler = (math.radians(4), math.radians(-9), math.radians(6))

b3.objeto("Peana", b3.bm_revolucion([(0.36, -0.05), (0.37, -0.03), (0.36, 0.0), (0.0, 0.0)], 96), PEANA)

# ---------------- escena ----------------
b3.camara("FM_Cam", (0.55, -2.5, 0.85), (0, 0, 0.55), lente=50)
b3.estudio((0, 0, 0.55), distancia=3.0, fuerza=1.1)
b3.mundo("#D7DCE2", 0.6)
b3.render("BLENDER_EEVEE", 1600, 1600, muestras=64, look="AgX - Medium High Contrast")
b3.resplandor(0.2, 1.5, 5)
for scr in bpy.data.screens:
    for a in scr.areas:
        if a.type == "VIEW_3D":
            a.spaces.active.shading.type = "RENDERED"
            a.spaces.active.region_3d.view_perspective = "CAMERA"

result = {"objetos": len(col.all_objects)}
