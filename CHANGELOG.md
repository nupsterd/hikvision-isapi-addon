# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versionado siguiendo [SemVer](https://semver.org/lang/es/).

## [1.0.6] - 2026-06-14

### Fixed
- Mapeo invertido de eventos `(5, 25)` y `(5, 26)` del Access Controller:
  - `(5, 25)` antes decía "Door Open (autorizado)", ahora dice "Door Closed (sensor)". Corresponde al sensor magnético detectando el cierre físico de la puerta.
  - `(5, 26)` antes decía "Door Closed (sensor)", ahora dice "Door Open (sensor)". Corresponde al sensor magnético detectando la apertura física de la puerta.
- El error generaba notificaciones engañosas en HA: el residente recibía "Door Closed" cuando la puerta se acababa de abrir, y viceversa.

### Validation
- Validado empíricamente vía spike HTTP Listening contra DS-K2624X firmware V1.7.2 build 250210.
- Hardware de test: sensor magnético DS-PD1-MC-WS cableado con resistencia EOL 1kΩ en serie, `magneticType: alwaysClose` en config de la puerta.
- Tests confirmados: apertura legítima (Remote Unlock + apertura física), apertura forzada (separación de armadura sin autorización previa), cierre de puerta físico.

## [1.0.5] - 2026-06-13

### Added
- Nuevos eventos identificados experimentalmente con sensor magnético
  Hikvision DS-PD1-MC-WS conectado:
  - `(5, 25)` Door Open (autorizado, tras Remote Unlock o Exit Button)
  - `(5, 26)` Door Closed (sensor detecta imanes juntos)
  - `(5, 27)` Door Forced Open (intrusión: puerta abierta sin autorización)
  - `(5, 28)` Door Open Timeout (puerta abierta más del tiempo configurado)

## [1.0.4] - 2026-06-13

### Added
- Eventos `(5, 23)` Exit Button Pressed y `(5, 24)` Exit Button Released
  agregados al diccionario.
- Zona horaria `America/Bogota` configurada en el container Docker para
  alinear timestamps del log con la hora local.

### Fixed
- Watchdog del stream: detecta conexiones zombie (conectado pero sin
  datos) usando timeout de lectura de 60s. Hikvision emite heartbeats
  `videoloss` cada ~10s, así que silencio mayor a 60s indica problema.
  Antes el stream podía quedarse colgado silenciosamente.

### Removed
- Función `iter_events_from_stream()` (lógica embebida en `run()` para
  permitir el watchdog).

## [1.0.3] - 2026-06-12

### Removed
- Logs de diagnóstico de password (`pwd_len`, `pwd_repr`) ya no necesarios
  tras validación end-to-end.

### Changed
- Webhook por defecto renombrado a `hikvision_access_event` en lugar
  del `hik_test_event` usado durante desarrollo.

## [1.0.2] - 2026-06-12

### Fixed
- Manejo específico de respuestas 401 para evitar amplificar lockouts de
  cuenta de la controladora Hikvision. Si la controladora responde con
  `<lockStatus>lock</lockStatus>`, el listener espera el tiempo indicado
  por `<unlockTime>` antes de reintentar. Si es 401 sin lockout (password
  incorrecto), espera 5 minutos antes de reintentar en lugar de bucle de 5s.

## [1.0.1] - 2026-06-12

### Changed
- Logs de diagnóstico adicionales: longitud del password leído y dump del header
  `WWW-Authenticate` cuando hay 401, para depurar fallos de autenticación digest.

## [1.0.0] - 2026-06-12

### Added
- Listener inicial del Event Stream ISAPI de controladores Hikvision DS-K2624X.
- Reenvío filtrado a webhook de Home Assistant (Opción C):
  - Eventos `majorEventType=5` (control de acceso) → HA
  - Eventos `(majorEventType, subEventType)=(3, 1024)` (Remote Unlock) → HA
  - Resto de eventos → solo audit log local
- Audit log JSON Lines en `/data/audit.log` con todos los eventos recibidos.
- Reconexión automática con backoff configurable ante caídas.
- Diccionario de eventos conocidos con descripciones legibles.

### Notas
- Pensado para coexistir con futura BD de auditoría unificada
  (PostgreSQL/Timescale para los 3 módulos del Sistema de Portería Virtual).
