# seerr-bridge

Puente entre **[Seerr](https://github.com/seerr-team/seerr)** (peticiones de películas y series) y **[XtreamFilter](https://github.com/SpanishST/xtreamfilter)** (catálogo de un proveedor IPTV y cola de descargas). Hace el trabajo que normalmente harían Radarr y Sonarr, pero usando el catálogo del proveedor.

**Versión 1** (1.2.2)

## Qué hace
- **Peticiones de Seerr**: busca cada petición en el catálogo del proveedor (por número de TMDB) y elige la versión en español:
  1. grupo **ES**, 2. grupo **LA** (latino), 3. otra versión con **audio en español**, 4. otra con **subtítulos completos en español** (análisis con ffprobe).
- **Aprueba o rechaza** las peticiones de los clientes según haya o no versión en español, y **avisa al cliente en Jellyfin** (mensajes personalizables).
- Añade lo pedido a la **cola de XtreamFilter**, al principio de la cola, sin duplicar lo que ya tienes (comprueba Jellyfin y la cola).
- Las **series en emisión** se añaden al seguimiento de XtreamFilter para descargar los episodios nuevos.
- Al terminar cada descarga: actualiza **Jellyfin** y (opcional) la envía a **shrinkerr** y la sube al principio de su cola.
- **Reintenta** automáticamente las descargas con error de la cola.
- **Episodios que faltan**: una vez al día (en la franja horaria elegida) compara las series del disco con el proveedor y muestra o añade a la cola lo que falta. Recuerda las series terminadas y completas para no revisarlas a diario.
- Como el proveedor suele admitir **una sola conexión**, pausa la cola de XtreamFilter mientras analiza archivos o consulta al proveedor, y la reanuda después.
- Página web de control (puerto 5056) con peticiones, episodios que faltan, registro y ajustes.

## Requisitos
- Docker (recomendado Portainer).
- Seerr, XtreamFilter y Jellyfin. Opcional: shrinkerr.
- El proveedor debe ofrecer el número de TMDB en su catálogo.

## Instalación rápida
1. Crear `/opt/seerr-bridge/app` y `/opt/seerr-bridge/data` (dueño: usuario 1000).
2. Copiar `app/bridge.py` y `app/index.html` a `/opt/seerr-bridge/app`.
3. Desplegar [`docker-compose.yml`](docker-compose.yml) como stack en Portainer.
4. Abrir `http://IP-DEL-SERVIDOR:5056` → **⚙ Ajustes** → conexiones (Seerr, Jellyfin, XtreamFilter, lista de títulos) → Probar conexión → Guardar.
5. Probar en **modo prueba** y, cuando esté bien, pasar a modo real.

Guía completa: [INSTALACION.md](INSTALACION.md) · Ayuda y explicación de cada ajuste: [AYUDA.md](AYUDA.md) · Cambios: [CHANGELOG.md](CHANGELOG.md)

## Notas
- Los valores por defecto de las direcciones (`10.10.10.x`) son de ejemplo: cámbialos en **⚙ Ajustes** por los de tu red.
- La página no tiene contraseña: úsala solo dentro de tu red.
- Los ajustes y claves de API se guardan en `/opt/seerr-bridge/data` y **no** forman parte del repositorio.
