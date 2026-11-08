# Vídeo de 10 s del señor Juan dando puñetazos mientras la cámara da una vuelta completa a su alrededor.
#   buho-blender run video_360.py --fondo --archivo funko_juan.blend
import math
import os

import bpy
from mathutils import Vector

AQUI = r"C:\Users\jferc\Documents\Bussines\BUHO\repo auxiliar\BuhoAI-Blender-CLI\modelos\funko_juan"
SEG, FPS = 10, 24
VUELTAS_PUNOS = math.ceil(SEG * FPS / 48)
exec(open(os.path.join(AQUI, "animar_punetazos.py"), encoding="utf8").read())

sc = bpy.context.scene
sc.frame_start, sc.frame_end = 1, SEG * FPS
col = bpy.data.collections["FunkoJuan"]

# Pivote en el centro de la figura; la cámara cuelga de él y el pivote gira 360° en lineal.
piv = bpy.data.objects.get("Orbita") or bpy.data.objects.new("Orbita", None)
if piv.name not in col.objects:
    col.objects.link(piv)
piv.location = (0, 0, 0.5)
piv.animation_data_clear()
cam = sc.camera
cam.parent = None
cam.location = (0, -2.55, 0.92)
cam.rotation_euler = (Vector((0, 0, 0.55)) - cam.location).to_track_quat("-Z", "Y").to_euler()
bpy.context.view_layer.update()
mw = cam.matrix_world.copy()
cam.parent = piv
cam.matrix_parent_inverse = piv.matrix_world.inverted()
cam.matrix_world = mw
# las luces giran con la cámara para que la figura se vea igual de bien por detrás
for o in col.objects:
    if o.type == "LIGHT" and o.parent is None:
        m = o.matrix_world.copy()
        o.parent = piv
        o.matrix_parent_inverse = piv.matrix_world.inverted()
        o.matrix_world = m
piv.rotation_euler = (0, 0, 0)
piv.keyframe_insert("rotation_euler", index=2, frame=1)
piv.rotation_euler = (0, 0, 2 * math.pi)
piv.keyframe_insert("rotation_euler", index=2, frame=sc.frame_end + 1)
for fc in getattr(piv.animation_data.action, "fcurves", []):
    for k in fc.keyframe_points:
        k.interpolation = "LINEAR"

sc.render.resolution_x = sc.render.resolution_y = 1080
sc.eevee.taa_render_samples = 32
s = sc.render.image_settings
try:
    s.media_type = "VIDEO"
except Exception:
    pass
s.file_format = "FFMPEG"
sc.render.ffmpeg.format = "MPEG4"
sc.render.ffmpeg.codec = "H264"
sc.render.ffmpeg.constant_rate_factor = "HIGH"
sc.render.use_file_extension = False
sc.render.filepath = os.path.join(AQUI, "funko_juan_punetazos_360.mp4")
bpy.ops.wm.save_as_mainfile(filepath=os.path.join(AQUI, "funko_juan_punetazos_360.blend"))
bpy.ops.render.render(animation=True)
result = {"video": sc.render.filepath, "segundos": SEG, "bytes": os.path.getsize(sc.render.filepath)}
