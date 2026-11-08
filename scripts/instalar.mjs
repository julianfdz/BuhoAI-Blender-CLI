// Instala el shim buho-blender.cmd en %LOCALAPPDATA%\Programs\BuhoBlender\cli y lo añade al PATH del usuario.
import fs from "node:fs";
import path from "node:path";
import { execSync } from "node:child_process";
import { fileURLToPath } from "node:url";
const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const dir = path.join(process.env.LOCALAPPDATA, "Programs", "BuhoBlender", "cli");
fs.mkdirSync(dir, { recursive: true });
fs.writeFileSync(path.join(dir, "buho-blender.cmd"), `@echo off\r\nnode "${path.join(repo, "bin", "buho-blender.mjs")}" %*\r\n`);
const actual = execSync('powershell -NoProfile -Command "[Environment]::GetEnvironmentVariable(\'Path\',\'User\')"').toString().trim();
if (!actual.toLowerCase().split(";").includes(dir.toLowerCase())) {
  execSync(`powershell -NoProfile -Command "[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path','User') + ';${dir}', 'User')"`);
  console.log("Añadido al PATH del usuario:", dir);
} else console.log("Ya estaba en el PATH:", dir);
console.log("Instalado:", path.join(dir, "buho-blender.cmd"));
