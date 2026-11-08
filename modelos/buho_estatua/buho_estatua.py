# Estatua de búho (navy con pátina dorada y ojos ámbar), a partir de la imagen de referencia de Julián.
# buho-blender run buho_estatua.py  [--fondo]   · idempotente: rehace su colección, cámara, luces y mundo.
# Unidades en metros (unos 2,1 m de alto). Mira hacia -Y.

import math
import bpy
import bmesh
from mathutils import Matrix, Vector

COL = "BuhoEstatua"
sc = bpy.context.scene

# Archivo nuevo de fábrica: quita el cubo, la luz y la cámara por defecto.
if not bpy.data.filepath:
    for n in ("Cube", "Light", "Camera"):
        o = bpy.data.objects.get(n)
        if o:
            bpy.data.objects.remove(o, do_unlink=True)

if COL in bpy.data.collections:
    col = bpy.data.collections[COL]
    for o in list(col.all_objects):
        bpy.data.objects.remove(o, do_unlink=True)
else:
    col = bpy.data.collections.new(COL)
    sc.collection.children.link(col)
for m in list(bpy.data.meshes):
    if m.users == 0 and m.name.startswith("be_"):
        bpy.data.meshes.remove(m)


def srgb(h):
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c)


# ---------- materiales ----------
def bronce(name, escala_escamas=18.0, oro=0.42, azul="#0B2E7A"):
    """Metal oscuro azul con pátina dorada en manchas y relieve de escamas (Voronoi)."""
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    tc = nt.nodes.new("ShaderNodeTexCoord")
    # Escamas
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    vor.feature = "DISTANCE_TO_EDGE"
    vor.inputs["Scale"].default_value = escala_escamas
    nt.links.new(tc.outputs["Object"], vor.inputs["Vector"])
    bordes = nt.nodes.new("ShaderNodeMapRange")
    bordes.inputs["From Min"].default_value = 0.0
    bordes.inputs["From Max"].default_value = 0.08
    nt.links.new(vor.outputs["Distance"], bordes.inputs["Value"])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.55
    bump.inputs["Distance"].default_value = 0.01
    nt.links.new(bordes.outputs["Result"], bump.inputs["Height"])
    # Pátina dorada: ruido grande + las juntas de las escamas
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 4.0
    noise.inputs["Detail"].default_value = 8.0
    noise.inputs["Roughness"].default_value = 0.65
    nt.links.new(tc.outputs["Object"], noise.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = oro
    ramp.color_ramp.elements[1].position = oro + 0.12
    nt.links.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    junta = nt.nodes.new("ShaderNodeMath")
    junta.operation = "LESS_THAN"
    junta.inputs[1].default_value = 0.015
    nt.links.new(vor.outputs["Distance"], junta.inputs[0])
    junta_suave = nt.nodes.new("ShaderNodeMath")
    junta_suave.operation = "MULTIPLY"
    junta_suave.inputs[1].default_value = 0.12
    nt.links.new(junta.outputs[0], junta_suave.inputs[0])
    mezcla_f = nt.nodes.new("ShaderNodeMath")
    mezcla_f.operation = "MAXIMUM"
    nt.links.new(ramp.outputs["Color"], mezcla_f.inputs[0])
    nt.links.new(junta_suave.outputs[0], mezcla_f.inputs[1])
    # Base: negro azulado con vetas azul intenso
    vetas = nt.nodes.new("ShaderNodeTexNoise")
    vetas.inputs["Scale"].default_value = 1.6
    nt.links.new(tc.outputs["Object"], vetas.inputs["Vector"])
    base = nt.nodes.new("ShaderNodeMix")
    base.data_type = "RGBA"
    base.inputs[6].default_value = (*srgb("#05070D"), 1)
    base.inputs[7].default_value = (*srgb(azul), 1)
    nt.links.new(vetas.outputs["Fac"], base.inputs[0])
    col_fin = nt.nodes.new("ShaderNodeMix")
    col_fin.data_type = "RGBA"
    nt.links.new(mezcla_f.outputs[0], col_fin.inputs[0])
    nt.links.new(base.outputs[2], col_fin.inputs[6])
    col_fin.inputs[7].default_value = (*srgb("#B8904A"), 1)
    nt.links.new(col_fin.outputs[2], bsdf.inputs["Base Color"])
    rough = nt.nodes.new("ShaderNodeMapRange")
    rough.inputs["To Min"].default_value = 0.28
    rough.inputs["To Max"].default_value = 0.5
    nt.links.new(mezcla_f.outputs[0], rough.inputs["Value"])
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])
    bsdf.inputs["Metallic"].default_value = 0.85
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m


def liso(name, color, metal=0.0, rough=0.5, emit=None, strength=0.0, coat=0.0):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Metallic"].default_value = metal
    b.inputs["Roughness"].default_value = rough
    b.inputs["Coat Weight"].default_value = coat
    if emit:
        b.inputs["Emission Color"].default_value = (*emit, 1)
        b.inputs["Emission Strength"].default_value = strength
    return m


CUERPO = bronce("BE_Cuerpo", 42.0, 0.66)
PLUMAS = bronce("BE_Alas", 60.0, 0.7, azul="#1340A8")
CABEZA = bronce("BE_Cabeza", 48.0, 0.5)
ORO = liso("BE_Oro", srgb("#9C7A3C"), metal=1.0, rough=0.38)
IRIS = liso("BE_Iris", srgb("#FFC400"), rough=0.25, emit=srgb("#FFB000"), strength=1.8)
PUPILA = liso("BE_Pupila", srgb("#020202"), rough=0.08, coat=1.0)


# ---------- geometría ----------
def obj(name, bm, material, loc=(0, 0, 0), rot=(0, 0, 0), subsurf=0, bevel=0.0, smooth=True):
    me = bpy.data.meshes.new("be_" + name)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = smooth
    o = bpy.data.objects.new(name, me)
    o.location = loc
    o.rotation_euler = [math.radians(a) for a in rot]
    o.data.materials.append(material)
    col.objects.link(o)
    if bevel:
        md = o.modifiers.new("Bevel", "BEVEL")
        md.width = bevel
        md.segments = 3
    if subsurf:
        md = o.modifiers.new("Subdiv", "SUBSURF")
        md.levels = subsurf
        md.render_levels = subsurf
    return o


def esfera(r=1.0, u=48, v=24):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=v, radius=r)
    return bm


def caja(sx, sy, sz, cortes=0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    if cortes:
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cortes, use_grid_fill=True)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    return bm


def cilindro(r1, r2, h, segs=48):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segs, radius1=r1, radius2=r2, depth=h)
    return bm


def toro(R, r, segs=64, rsegs=16):
    bm = bmesh.new()
    rings = []
    for i in range(segs):
        a = 2 * math.pi * i / segs
        c = Vector((math.cos(a) * R, math.sin(a) * R, 0))
        d = Vector((math.cos(a), math.sin(a), 0))
        rings.append([bm.verts.new(c + d * math.cos(b) * r + Vector((0, 0, math.sin(b) * r)))
                      for b in (2 * math.pi * j / rsegs for j in range(rsegs))])
    for i in range(segs):
        a, b = rings[i], rings[(i + 1) % segs]
        for j in range(rsegs):
            bm.faces.new((a[j], a[(j + 1) % rsegs], b[(j + 1) % rsegs], b[j]))
    return bm


# Cuerpo: huevo alto, más ancho abajo, con el pecho algo plano.
bm = esfera(1.0, 64, 32)
for v in bm.verts:
    z = v.co.z
    ancho = 1.0 + 0.12 * (-z)          # más ancho abajo
    v.co.x *= 0.56 * ancho
    v.co.y *= 0.46 * ancho
    if v.co.y < 0:
        v.co.y *= 0.85                 # pecho más plano
    v.co.z *= 0.86
obj("Cuerpo", bm, CUERPO, loc=(0, 0, 0.98), subsurf=1)

# Cabeza: bloque con la parte de arriba plana y las «orejas» en las esquinas.
bm = caja(0.74, 0.54, 0.58, cortes=4)
for v in bm.verts:
    t = (v.co.z + 0.29) / 0.58          # 0 abajo, 1 arriba
    v.co.x *= 0.9 + 0.1 * t              # se abre hacia arriba
    if t > 0.95:
        v.co.z += 0.07 * (abs(v.co.x) / 0.37) ** 3   # esquinas levantadas
        v.co.z -= 0.03 * (1 - abs(v.co.x) / 0.37)    # centro algo hundido
    if v.co.y < 0:
        v.co.y *= 0.9
obj("Cabeza", bm, CABEZA, loc=(0, 0.02, 1.8), bevel=0.05)

# Discos faciales: dos cuencos de plumas alrededor de los ojos.
for s in (-1, 1):
    bm = cilindro(0.18, 0.14, 0.06, 64)
    obj(f"Disco_{'D' if s > 0 else 'I'}", bm, PLUMAS, loc=(s * 0.175, -0.255, 1.79), rot=(90, 0, s * -8), bevel=0.02)
    # Ojo: iris ámbar luminoso y pupila negra brillante.
    obj(f"Iris_{'D' if s > 0 else 'I'}", esfera(0.095), IRIS, loc=(s * 0.175, -0.27, 1.79))
    bm = esfera(0.058)
    bmesh.ops.scale(bm, vec=(1, 0.5, 1), verts=bm.verts)
    obj(f"Pupila_{'D' if s > 0 else 'I'}", bm, PUPILA, loc=(s * 0.175, -0.345, 1.79))
    # Ceja: arista en V que baja hacia el pico (le da el gesto serio).
    obj(f"Ceja_{'D' if s > 0 else 'I'}", caja(0.3, 0.05, 0.03), ORO, loc=(s * 0.17, -0.29, 1.935),
        rot=(0, -s * 16, 0), bevel=0.012)

# Pico: cono pequeño entre los ojos, apuntando abajo y adelante.
bm = cilindro(0.045, 0.004, 0.16, 24)
bmesh.ops.scale(bm, vec=(1, 0.75, 1), verts=bm.verts)
obj("Pico", bm, ORO, loc=(0, -0.3, 1.71), rot=(160, 0, 0))

# Alas plegadas a los lados, largas, con plumas más finas.
for s in (-1, 1):
    bm = esfera(1.0, 48, 24)
    for v in bm.verts:
        v.co.x *= 0.12
        v.co.y *= 0.32
        v.co.z *= 0.78
        if v.co.x * s < 0:
            v.co.x *= 0.4                # cara interior pegada al cuerpo
    obj(f"Ala_{'D' if s > 0 else 'I'}", bm, PLUMAS, loc=(s * 0.44, 0.03, 1.0), rot=(0, s * -5, 0), subsurf=1)

# Anillo dorado bajo el pecho y peana.
bm = toro(0.52, 0.035)
bmesh.ops.scale(bm, vec=(1, 0.86, 1), verts=bm.verts)
obj("Anillo", bm, ORO, loc=(0, -0.02, 0.36))
obj("Peana", cilindro(0.6, 0.66, 0.22, 64), CUERPO, loc=(0, 0, 0.11), bevel=0.03)

# ---------- cámara, luces y mundo (contrapicado oscuro como la referencia) ----------
for n in ("BE_Cam", "BE_Clave", "BE_RimI", "BE_RimD", "BE_Ojos"):
    o = bpy.data.objects.get(n)
    if o:
        bpy.data.objects.remove(o, do_unlink=True)

cd = bpy.data.cameras.new("BE_Cam")
cd.lens = 35
cam = bpy.data.objects.new("BE_Cam", cd)
col.objects.link(cam)
cam.location = (0, -3.9, 0.6)
cam.rotation_euler = (Vector((0, 0, 1.3)) - cam.location).to_track_quat("-Z", "Y").to_euler()
sc.camera = cam


def luz(nombre, tipo, energia, color, loc, mira=(0, 0, 1.3), tam=1.0):
    ld = bpy.data.lights.new(nombre, tipo)
    ld.energy = energia
    ld.color = color
    if tipo == "AREA":
        ld.size = tam
    o = bpy.data.objects.new(nombre, ld)
    o.location = loc
    o.rotation_euler = (Vector(mira) - Vector(loc)).to_track_quat("-Z", "Y").to_euler()
    col.objects.link(o)
    return o


luz("BE_Clave", "AREA", 260, (1.0, 0.86, 0.68), (-1.2, -2.2, 3.2), tam=1.2)
luz("BE_RimI", "AREA", 500, (0.35, 0.5, 1.0), (-2.0, 1.6, 2.2), tam=1.5)
luz("BE_RimD", "AREA", 500, (0.35, 0.5, 1.0), (2.0, 1.6, 2.2), tam=1.5)
luz("BE_Ojos", "POINT", 6, (1.0, 0.7, 0.2), (0, -0.6, 1.8))

w = sc.world or bpy.data.worlds.new("World")
sc.world = w
w.use_nodes = True
bg = w.node_tree.nodes.get("Background")
bg.inputs["Color"].default_value = (*srgb("#03060C"), 1)
bg.inputs["Strength"].default_value = 1.0

sc.render.engine = "BLENDER_EEVEE"
sc.render.resolution_x, sc.render.resolution_y = 1672, 941
try:
    sc.view_settings.view_transform = "AgX"
    sc.view_settings.look = "AgX - Punchy"
except Exception:
    pass

result = {"coleccion": COL, "piezas": len([o for o in col.objects if o.type == "MESH"])}
