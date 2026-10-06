# seerr-bridge

Puente entre **[Seerr](https://github.com/seerr-team/seerr)** (peticiones de películas y series) y **[XtreamFilter](https://github.com/SpanishST/xtreamfilter)** (catálogo de un proveedor IPTV y cola de descargas). Hace el trabajo que normalmente harían Radarr y Sonarr, pero usando el catálogo del proveedor.

**Versión 3** (1.2.12)

## Cómo encaja cada pieza

| Programa | Qué hace en este montaje |
|---|---|
| **[Seerr](https://github.com/seerr-team/seerr)** | La web donde los clientes piden películas y series. |
| **seerr-bridge** | Revisa las peticiones, busca la versión en tu idioma en el catálogo del proveedor, aprueba o rechaza, y manda descargar. |
| **[XtreamFilter](https://github.com/SpanishST/xtreamfilter)** | Filtra el catálogo del proveedor IPTV (solo los grupos que quieres) y descarga lo que hay en su cola. |
| **[Jellyfin](https://github.com/jellyfin/jellyfin)** | Muestra lo descargado. El puente lo usa para saber qué tienes ya, para actualizar la biblioteca y para avisar al cliente. |
| **[shrinkerr](https://github.com/i-ial9000/shrinkerr)** (opcional) | Convierte lo descargado a un formato más ligero (por ejemplo H.265) para que ocupe menos disco. El puente le envía cada descarga terminada y la sube al principio de su cola. |

## El recorrido de una petición

```
 Cliente pide "Duna" en Seerr
          │
          ▼
 ┌──────────────┐   cada 5 min   ┌───────────────┐   busca por TMDB   ┌──────────────────────┐
 │    Seerr     │ ─────────────► │ seerr-bridge  │ ─────────────────► │ catálogo XtreamFilter│
 │  (petición)  │ ◄───────────── │               │ ◄───────────────── │ (proveedor filtrado) │
 └──────────────┘ aprueba/rechaza└───────────────┘   versiones        └──────────────────────┘
                                        │
                                        │ ¿ya está en Jellyfin o en la cola? → no se duplica
                                        ▼
                                ┌───────────────┐   descarga   ┌──────────────┐
                                │ cola de       │ ───────────► │    disco     │
                                │ XtreamFilter  │              └──────┬───────┘
                                └───────────────┘                     │
                                                                      ▼
                    ┌──────────────┐   (opcional)   ┌───────────────┐   actualiza
                    │  shrinkerr   │ ◄───────────── │ seerr-bridge  │ ───────────► Jellyfin
                    │ (convierte)  │                └───────────────┘                │
                    └──────────────┘                                                 ▼
                                                              Seerr lo marca "Disponible"
                                                              y el cliente recibe el aviso
```

## Qué hace

### Peticiones de Seerr
Cada petición se busca en el catálogo del proveedor por su **número de TMDB** (da igual que el título esté en inglés o en español). Entre las versiones encontradas (sin 4K si la regla está activa) elige en este orden:

```
¿Alguna versión empieza por uno de tus prefijos?  (p. ej. "ES -","LA -","ESP","ES-")
   ├─ Sí → se elige y se deja de buscar
   └─ No → ¿alguna tiene audio en tus idiomas?  (p. ej. SPA)
             ├─ Sí → se elige
             └─ No → ¿alguna tiene subtítulos completos en tus idiomas?
                       ├─ Sí → se elige
                       └─ No → se rechaza y se avisa al cliente
```

- **Prefijos**: se comparan con el principio del título o del grupo, tal cual los escribes. El orden de la lista es la preferencia; si varias versiones coinciden, gana la más reciente.
- **Audio y subtítulos**: se leen las pistas de cada versión con **ffprobe**. Puedes elegir si cuenta **cualquier pista**, **primero la principal** o **solo la principal**.
- En la columna **Detalle** se ve siempre por qué se eligió: *"(prefijo «ES -» en el grupo)"*, *"(audio SPA)"* o *"(subtítulos SPA)"*.
- **Series: solo cuentan las versiones que tienen lo pedido.** Si la versión con prefijo no tiene la temporada pedida, se analizan las demás versiones que sí la tienen (NF, EN, MAX…), buscando audio y después subtítulos en tus idiomas. Ejemplo: *After Life* T3 solo existía en `NF - After Life`: se analizó y se descargó.
- **Misma carpeta**: si la serie ya está en Jellyfin, los episodios nuevos se guardan en **su carpeta**, aunque vengan de otra versión.
- **Protección del año**: se descartan versiones de otro idioma cuyo año no coincide con TMDB (errores del catálogo).
- **Aprueba o rechaza** la petición en Seerr y **avisa al cliente en Jellyfin** (mensajes personalizables).
- Añade lo pedido a la **cola de XtreamFilter**, al principio si quieres, sin duplicar lo que ya tienes (comprueba Jellyfin y la cola).
- Las **series en emisión** se añaden al seguimiento de XtreamFilter para descargar los episodios nuevos.

### Al terminar cada descarga
- Pide a **Jellyfin** que actualice la biblioteca.
- Opcional: la envía a **shrinkerr** y la sube al principio de su cola.
- **Reintenta** las descargas con error, hasta el número de veces que elijas.

### Episodios que faltan
Una vez al día, en la franja horaria que elijas (o con "Buscar ahora"):
- Consulta solo las series que **Seerr marca como incompletas**. Una consulta cada 5 s, y se para si el proveedor falla 2 veces seguidas.
- No vuelve a añadir lo que ya está en Jellyfin o en la cola.
- Muestra lo que falta o lo añade a la cola automáticamente.
- Mientras busca, pausa la cola de XtreamFilter y espera 15 s antes y después. Al terminar reintenta las descargas con error y reanuda la cola.
- Si la búsqueda se cancela (el proveedor falla 2 veces seguidas), se vuelve a intentar a los **30 minutos**, dentro de la franja horaria. Un error de red o DNS se repite una vez tras 30 s.
- Estados de la lista: **Pendiente** → **En cola** → **Completada** (todo ya en Jellyfin) → se borra de la lista en la siguiente búsqueda.

### Una sola conexión con el proveedor
Muchos proveedores admiten **una sola conexión**. Antes de consultar al proveedor, el puente libera la conexión. Después deja la cola como estaba; si la pausaste tú, no la toca.

### Página de control
En el puerto 5056: peticiones, episodios que faltan, registro y ajustes. Los mensajes de estado llevan color: **azul** mientras trabaja, **verde** con el resultado al terminar, **rojo** si se canceló.

## Requisitos
- Docker (recomendado Portainer).
- [Seerr](https://github.com/seerr-team/seerr), [XtreamFilter](https://github.com/SpanishST/xtreamfilter) y [Jellyfin](https://github.com/jellyfin/jellyfin). Opcional: [shrinkerr](https://github.com/i-ial9000/shrinkerr).
- El proveedor debe dar el número de TMDB en su catálogo.
- Compatible con **Jellyfin 10 y 12** (desde la 1.2.10).

## Instalación rápida
1. Crear `/opt/seerr-bridge/app` y `/opt/seerr-bridge/data` (dueño: usuario 1000).
2. Copiar `app/bridge.py` y `app/index.html` a `/opt/seerr-bridge/app`.
3. Desplegar [`docker-compose.yml`](docker-compose.yml) como stack en Portainer.
4. Abrir `http://IP-DEL-SERVIDOR:5056` → **⚙ Ajustes** → conexiones (Seerr, Jellyfin, XtreamFilter, lista de títulos) → Probar conexión → Guardar.
5. Probar en **modo prueba** y, cuando esté bien, pasar a modo real.

Guía completa: [INSTALACION.md](INSTALACION.md) · Ayuda y explicación de cada ajuste: [AYUDA.md](AYUDA.md) · Cambios: [CHANGELOG.md](CHANGELOG.md)

## Notas
- Las direcciones por defecto (`10.10.10.x`) son de ejemplo: cámbialas en **⚙ Ajustes** por las de tu red.
- La página no tiene contraseña: úsala solo dentro de tu red.
- Los ajustes y las claves de API se guardan en `/opt/seerr-bridge/data` y **no** forman parte del repositorio.
