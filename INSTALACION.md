# Instalación de seerr-bridge 1.2.2

Guía para instalar seerr-bridge en Docker, desplegándolo como **stack de Portainer**.

---

## 1. Requisitos

| Servicio | Para qué | Obligatorio |
|---|---|---|
| **Seerr** | Web donde los clientes piden películas y series | Sí |
| **XtreamFilter** | Catálogo del proveedor IPTV y cola de descargas | Sí |
| **Jellyfin** | Saber qué hay en el disco, actualizar la biblioteca y avisar a los clientes | Sí (muy recomendado) |
| **shrinkerr** | Convertir (reducir) lo descargado | No |
| **Portainer** | Desplegar el stack | Recomendado |

En Seerr **no** hay que configurar Radarr ni Sonarr: su trabajo lo hace el puente.

Datos que necesitarás:
- **Clave de API de Seerr**: Seerr → Ajustes → General → "Clave API".
- **Clave de API de Jellyfin**: Jellyfin → Panel de control → Claves de API → **+** (nómbrala `seerr-bridge`).
- Las direcciones internas de Seerr, Jellyfin, XtreamFilter y (opcional) shrinkerr.

---

## 2. Crear las carpetas

El puente usa dos carpetas en el servidor:

| Carpeta | Contenido |
|---|---|
| `/opt/seerr-bridge/app` | El programa (`bridge.py` e `index.html`) |
| `/opt/seerr-bridge/data` | Ajustes y base de datos (se crea el contenido solo) |

Las dos deben pertenecer al **usuario 1000** (el usuario con el que funciona el contenedor).

En la **consola del servidor Docker**, como administrador (`root`), ejecuta:

```bash
mkdir -p /opt/seerr-bridge/app /opt/seerr-bridge/data
```

```bash
chown -R 1000:1000 /opt/seerr-bridge
```

Si no tienes acceso de administrador pero sí a Docker, este comando hace lo mismo con un contenedor temporal:

```bash
docker run --rm -v /opt:/host-opt busybox sh -c "mkdir -p /host-opt/seerr-bridge/app /host-opt/seerr-bridge/data && chown -R 1000:1000 /host-opt/seerr-bridge"
```

---

## 3. Copiar el programa

Copia los dos archivos de la carpeta `app/` de este proyecto a `/opt/seerr-bridge/app/`:

```bash
cp app/bridge.py app/index.html /opt/seerr-bridge/app/
```

Comprueba que están:

```bash
ls -la /opt/seerr-bridge/app
```

Debe mostrar `bridge.py` e `index.html`.

---

## 4. Desplegar el stack en Portainer

1. Portainer → **Stacks** → **Add stack**.
2. Nombre: `seerr-bridge`.
3. Pega el contenido de **`docker-compose.yml`** (en esta carpeta).
4. Revisa la zona horaria (`TZ`) y el puerto (`5056`) si quieres cambiarlos.
5. Pulsa **Deploy the stack**.

La imagen (`python:3.13-alpine`) se descarga sola. Al crear el contenedor instala **ffprobe** (unos 40 MB, necesario para analizar audio y subtítulos) y después funciona como usuario 1000. No hay que instalar nada más a mano.

Comprueba que funciona abriendo en el navegador:

```
http://IP-DEL-SERVIDOR:5056
```

Debe aparecer la página de seerr-bridge con la etiqueta **v1.2.2** y **MODO PRUEBA**.

---

## 5. Primera configuración

1. Pulsa **⚙ Ajustes** (arriba a la derecha).
2. Rellena:
   - **Seerr**: dirección y clave de API.
   - **Jellyfin**: dirección y clave de API.
   - **Lista de títulos (Xtream Codes)**: dirección `player_api.php` de XtreamFilter, usuario y contraseña (p. ej. `http://10.10.10.16:5000/merged/player_api.php`, `USER`, `PASS`).
   - **XtreamFilter**: dirección (para la cola, el historial y los seguimientos).
   - **Shrinkerr** (opcional): dirección.
3. Pulsa **Probar conexión** en cada apartado: deben salir en verde.
4. Revisa las **Reglas** (idioma `ES`, excluir 4K, etc.) y los **Mensajes al cliente**.
5. Pulsa **Guardar ajustes**.

### Probar en modo prueba

El puente arranca en **modo prueba**: no aprueba, no rechaza ni añade nada, solo muestra lo que haría.

1. Haz una petición en Seerr.
2. En la página del puente (**Peticiones**) verás en menos de 5 minutos qué haría (o pulsa **Revisar ahora**).
3. Cuando estés conforme: **⚙ Ajustes** → desmarca **Modo prueba** → **Guardar ajustes**. Arriba cambiará a **MODO REAL**.

### Permisos en Seerr (importante)

Para que el puente pueda **rechazar** lo que no está disponible, las peticiones de los clientes deben quedar **pendientes** (sin aprobación automática):

- Seerr → Ajustes → Usuarios → **Permisos por defecto**: dejar solo **Solicitar** (sin "Aprobación automática").
- Revisar también cada usuario existente.

Las peticiones del administrador de Seerr se aprueban siempre solas; el puente las procesa pero no puede rechazarlas.

---

## 6. Actualizar a una versión nueva

1. Copia los nuevos `bridge.py` e `index.html` a `/opt/seerr-bridge/app/` (sustituyendo los anteriores).
2. Reinicia el contenedor: Portainer → **Containers** → `seerr-bridge` → **Restart**.
3. Comprueba la versión en la cabecera de la página.

Los ajustes y la base de datos (`/opt/seerr-bridge/data`) se conservan; las tablas nuevas se crean solas al arrancar.

---

## 7. Copias de seguridad

Guarda la carpeta **`/opt/seerr-bridge/data`**:

| Archivo | Contenido |
|---|---|
| `settings.json` | Ajustes y claves de API (¡contiene secretos!) |
| `bridge.db` | Peticiones, elementos añadidos a la cola, avisos pendientes, episodios que faltan, series ignoradas y registro |

Para restaurar: para el contenedor, copia los archivos a `/opt/seerr-bridge/data` y vuelve a arrancarlo.

---

## 8. Desinstalar

1. Portainer → **Stacks** → `seerr-bridge` → **Delete this stack**.
2. (Opcional) Borrar la carpeta `/opt/seerr-bridge`.

Nada de Seerr, XtreamFilter, Jellyfin ni shrinkerr depende del puente: siguen funcionando sin él.
