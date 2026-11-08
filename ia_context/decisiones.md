# buho-blender · decisiones

- 08/11/2026: CLI propio en vez de depender solo del MCP. Usa el mismo puerto (9876) que el complemento oficial «MCP» de Blender Lab, así lo que hace el agente se ve en directo; con `--fondo` lanza `blender -b --factory-startup`.
- Blender portable en `D:\Programas\Blender` (5.2.3 LTS). El complemento solo abre el puerto si «Permitir acceso online» está activo.
- `captura` encuadra sola y pone cámara y luces temporales (`_buho_*`) que borra al acabar; `--coleccion` oculta el resto solo durante el render.
- `exportar --unir` une todo en una sola malla con materiales (lo que espera Unity/glTFast en Búho Zombies, que se queda con la pieza mayor y lo que la toca).
- Instalación: `npm run instalar` (shim en `%LOCALAPPDATA%\Programs\BuhoBlender\cli`, al PATH del usuario). Skill en `~/.claude/skills/buho-blender` y en el marketplace.
- Primer uso: Ray Gun propia de Búho Zombies (`D:\Unity\Proyectos\BuhoZombies\Blender\raygun_buho.py`).
