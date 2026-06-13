# Changelog

Formato basado en [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versionado siguiendo [SemVer](https://semver.org/lang/es/).

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
