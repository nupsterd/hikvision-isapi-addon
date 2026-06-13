# Hikvision ISAPI Listener — Home Assistant Add-on

Add-on para Home Assistant que se conecta al endpoint
`/ISAPI/Event/notification/alertStream` de un controlador de acceso
Hikvision (probado con DS-K2624X), parsea los eventos en tiempo real
y reenvía los relevantes a un webhook de Home Assistant.

Forma parte del proyecto **Sistema de Portería Virtual** de NupsterD.

## Cómo funciona

El controlador Hikvision expone un stream HTTP de larga duración con
todos los eventos del sistema en formato multipart MIME + JSON. Este
add-on:

1. Mantiene una conexión persistente al stream con autenticación Digest.
2. Parsea cada bloque MIME y normaliza el JSON a un dict plano.
3. **Filtra** los eventos relevantes y los reenvía como `POST` a un
   webhook de Home Assistant.
4. **Loguea TODOS** los eventos a `/data/audit.log` en formato JSON
   Lines para auditoría completa.
5. Reconecta automáticamente si pierde la conexión.

## Eventos reenviados a HA

| Filtro | Acción |
|---|---|
| `majorEventType = 5` (eventos de control de acceso) | Reenviar a HA |
| `(majorEventType, subEventType) = (3, 1024)` (Remote Unlock) | Reenviar a HA |
| Resto (logins, logouts, heartbeats, otros operativos) | Solo audit log |

Esta política se llama "Opción C" en la documentación interna del
proyecto: balancea **automations limpias** con **auditoría completa**.

## Limitaciones conocidas

- El controlador Hikvision permite **un solo cliente simultáneo** en el
  stream. Si iVMS-4200 está armado, este add-on no puede conectarse
  hasta que iVMS sea desarmado. Para producción, el add-on debe ser
  el único consumidor del stream.
- El `audit.log` no rota automáticamente. En producción se recomienda
  configurar `logrotate` o migrar la auditoría a PostgreSQL/Timescale.

## Configuración

| Opción | Tipo | Default | Descripción |
|---|---|---|---|
| `controller_host` | str | `192.168.18.70` | IP o hostname del controlador Hikvision |
| `controller_user` | str | `admin` | Usuario del controlador |
| `controller_password` | password | (vacío) | Contraseña del controlador |
| `ha_webhook_url` | url | (placeholder) | URL completa del webhook de HA |
| `audit_log_path` | str | `/data/audit.log` | Ruta del log de auditoría |
| `reconnect_delay` | int | `5` | Segundos entre intentos de reconexión |

## Instalación

1. En Home Assistant: **Settings → Add-ons → Add-on Store → ⋮ → Repositories**.
2. Agregar: `https://github.com/nupsterd/hikvision-isapi-addon`.
3. Instalar **Hikvision ISAPI Listener** desde el listado.
4. Configurar las opciones (especialmente `controller_password` y
   `ha_webhook_url`) y arrancar.

## Estructura del evento enviado a HA

Ejemplo de payload que recibe el webhook:

```json
{
  "kind": "access_controller_event",
  "timestamp": "2026-06-12T19:38:30-05:00",
  "device_ip": "192.168.18.70",
  "device_mac": "a4:d5:c2:72:56:50",
  "major": 5,
  "sub": 21,
  "description": "Door Unlocked",
  "door_no": 1,
  "door_name": "",
  "serial_no": 100,
  "card_no": null,
  "employee_no": null,
  "verify_mode": null,
  "raw": { ... evento original completo ... }
}
```

## Roadmap

- [ ] Escritura paralela del audit a PostgreSQL/Timescale (BD de
      auditoría unificada de los 3 módulos del Sistema de Portería).
- [ ] Rotación automática del audit log local.
- [ ] Endpoint de healthcheck para monitoreo externo.
- [ ] Enriquecer el diccionario `EVENT_TYPES` a medida que se descubren
      nuevos eventos en campo.

## Licencia

Uso interno del proyecto Sistema de Portería Virtual.
