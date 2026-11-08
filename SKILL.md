---
name: buho-blender
description: Modelar, editar, fotografiar y exportar modelos 3D en Blender con el CLI `buho-blender`, en directo en el Blender abierto (se ve lo que haces) o sin ventana para generar en lote. Úsala SIEMPRE que haya que crear o retocar un modelo 3D, un arma, un prop, un personaje o un escenario, sacar una captura de un .blend, exportar a GLB/FBX/OBJ para Unity o la web, o revisar qué hay en una escena de Blender, aunque no se nombre el CLI. Vale para cualquier agente con terminal (Claude, Codex, Copilot, Gemini, scripts).
---

# buho-blender

CLI propio de Búho AI para trabajar con Blender desde un agente. Dos modos:

- **En directo** (por defecto): habla con el Blender abierto por el puerto 9876 del complemento oficial «MCP» de Blender Lab. Lo que haces aparece en la ventana al momento. Si no hay ningún Blender abierto: `buho-blender abrir`.
- **Sin ventana** (`--fondo`): lanza `blender -b` con la escena de fábrica, o con `--archivo x.blend`. Para generar y exportar en lote sin tocar lo que el usuario tiene abierto.

Blender está en `D:\Programas\Blender\blender.exe` (portable, 5.2 LTS). Se cambia con `BUHO_BLENDER_PATH`.

## Comandos

```bash
buho-blender estado                      # versión, archivo, nº de objetos, motor
buho-blender escena                      # objetos con posición, tamaño, caras, materiales
buho-blender run script.py               # ejecuta Python (bpy); también "código" o - (entrada)
buho-blender captura salida.png --vista tres_cuartos   # frente|atras|derecha|izquierda|arriba|iso|camara
buho-blender exportar salida.glb         # o .fbx / .obj; --objetos "A,B" o --coleccion X
buho-blender guardar archivo.blend
buho-blender abrir [archivo.blend]
```

Opciones comunes: `--fondo`, `--archivo x.blend`, `--texto` (muestra también lo que imprima el script).

## Cómo trabajar

1. **Mira antes de tocar**: `estado` y `escena`. No borres ni cambies objetos del usuario sin preguntar.
2. **El modelo vive en un script**: escribe el modelado como un `.py` guardado junto al proyecto (no código suelto), para poder rehacerlo y versionarlo. El script debe ser idempotente: borra solo lo que él creó (por colección propia) y lo vuelve a crear.
3. **Devuelve datos con `result = {...}`**, no con `print`.
4. **Comprueba con los ojos**: `captura` desde dos o tres vistas y lee el PNG antes de dar algo por bueno. La captura encuadra sola los objetos y pone luces temporales; no deja nada en la escena.
5. **Exporta** a GLB para Unity (glTFast) o la web: Y arriba, escala aplicada, modificadores aplicados. Medidas reales en metros.

## Consejos de modelado

- Construye con primitivas + modificadores (Bevel, Subdivision, Mirror, Solidify, Boolean) y aplica antes de exportar si el destino es un motor de juego.
- Materiales con Principled BSDF; colores en lineal. Para emisivos usa `Emission Strength` (glTF lo exporta).
- Origen y orientación según el destino: para un arma en primera persona de Unity, cañón hacia +Y de Blender (que pasa a +Z en Unity) y el origen en la empuñadura.
- Polígonos razonables: un arma de juego, 2.000–15.000 triángulos.
