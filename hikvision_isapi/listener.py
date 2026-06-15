#!/usr/bin/env python3
"""
Hikvision ISAPI Event Stream Listener para Home Assistant.

Mantiene una conexión HTTP de larga duración al endpoint
/ISAPI/Event/notification/alertStream del controlador de acceso,
parsea los eventos multipart MIME + JSON, filtra los relevantes y
los reenvía a un webhook de Home Assistant. Loguea todo a archivo
local para auditoría completa.

Patrón inspirado en el add-on zkteco-adms-addon del mismo proyecto.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import signal
import sys
import time
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests
from requests.auth import HTTPDigestAuth


# ---------------------------------------------------------------------------
# Configuración: leída desde variables de entorno (inyectadas por config.yaml)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Config:
    """Configuración del listener. Read-only tras inicialización."""

    controller_host: str
    controller_user: str
    controller_password: str
    ha_webhook_url: str
    audit_log_path: Path
    reconnect_delay: int = 5

    @classmethod
    def from_options_json(cls, path: str = "/data/options.json") -> "Config":
        """Lee la config desde el archivo que inyecta el supervisor de HA."""
        with open(path, "r", encoding="utf-8") as f:
            opts = json.load(f)
        return cls(
            controller_host=opts["controller_host"],
            controller_user=opts["controller_user"],
            controller_password=opts["controller_password"],
            ha_webhook_url=opts["ha_webhook_url"],
            audit_log_path=Path(opts.get("audit_log_path", "/config/audit.log")),
            reconnect_delay=int(opts.get("reconnect_delay", 5)),
        )


# ---------------------------------------------------------------------------
# Diccionario de eventos Hikvision conocidos
# (major_event_type, sub_event_type) -> descripción humana
# ---------------------------------------------------------------------------

EVENT_TYPES: dict[tuple[int, int], str] = {
    # Major 3: Operation Events
    (3, 112): "Admin Login Success",
    (3, 113): "Admin Logout",
    (3, 121): "Admin Login Attempt",
    (3, 122): "Admin Login Failed",
    (3, 1024): "Remote Unlock (acción admin)",

    # Major 5: Access Control Events
    (5, 1): "Authentication Passed",
    (5, 2): "Authentication Failed",
    (5, 21): "Door Unlocked (relé)",
    (5, 22): "Door Locked (relé)",
    (5, 23): "Exit Button Pressed",
    (5, 24): "Exit Button Released",
    (5, 25): "Door Closed (sensor)",
    (5, 26): "Door Open (sensor)",
    (5, 27): "Door Forced Open (intrusión)",
    (5, 28): "Door Open Timeout",
    (5, 30): "Door Forced Open (legacy)",
    (5, 31): "Door Open Timeout (legacy)",
}

# Eventos que se reenvían a HA (Opción C):
#  - Todo Major 5 (acceso real)
#  - Solo (3, 1024) de Major 3 (remote unlock administrativo)
# El resto se loguea localmente pero NO se envía a HA.
def should_forward_to_ha(major: int, sub: int) -> bool:
    if major == 5:
        return True
    if (major, sub) == (3, 1024):
        return True
    return False


# ---------------------------------------------------------------------------
# Parser del stream multipart MIME
# ---------------------------------------------------------------------------

MIME_BOUNDARY = b"--MIME_boundary"


def parse_event_block(body: bytes, log: logging.Logger) -> Optional[dict]:
    """
    Parsea un bloque JSON del stream. Retorna dict normalizado o None.
    None significa: heartbeat (videoloss) — se ignora silenciosamente.
    """
    try:
        text = body.decode("utf-8", errors="replace").strip()
        if not text:
            return None
        event = json.loads(text)
    except json.JSONDecodeError:
        log.warning("No se pudo parsear JSON del bloque: %r", body[:100])
        return None

    event_type = event.get("eventType")

    # Heartbeats — se ignoran
    if event_type == "videoloss":
        return None

    # Eventos de control de acceso (los más importantes)
    if event_type == "AccessControllerEvent":
        ace = event.get("AccessControllerEvent", {})
        major = ace.get("majorEventType")
        sub = ace.get("subEventType")
        return {
            "kind": "access_controller_event",
            "timestamp": event.get("dateTime"),
            "device_ip": event.get("ipAddress"),
            "device_mac": event.get("macAddress"),
            "major": major,
            "sub": sub,
            "description": EVENT_TYPES.get(
                (major, sub), f"Unknown ({major},{sub})"
            ),
            "door_no": ace.get("doorNo"),
            "door_name": ace.get("doorName"),
            "serial_no": ace.get("serialNo"),
            "card_no": ace.get("cardNo"),
            "employee_no": ace.get("employeeNoString")
                          or ace.get("employeeNo"),
            "verify_mode": ace.get("currentVerifyMode"),
            "raw": event,
        }

    # Cualquier otro tipo: lo dejamos crudo
    return {
        "kind": "other",
        "timestamp": event.get("dateTime"),
        "event_type": event_type,
        "raw": event,
    }


def iter_events_from_stream(response: requests.Response, log: logging.Logger):
    """
    Generator que produce eventos parseados desde el stream HTTP.
    Maneja el buffer de chunks y el split por boundary MIME.
    """
    buffer = b""
    for chunk in response.iter_content(chunk_size=1024):
        if not chunk:
            continue
        buffer += chunk

        while MIME_BOUNDARY in buffer:
            part, _, buffer = buffer.partition(MIME_BOUNDARY)
            if not part.strip():
                continue

            # Separar headers HTTP del body (línea vacía)
            if b"\r\n\r\n" in part:
                _, _, body = part.partition(b"\r\n\r\n")
            elif b"\n\n" in part:
                _, _, body = part.partition(b"\n\n")
            else:
                continue

            event = parse_event_block(body, log)
            if event is not None:
                yield event


# ---------------------------------------------------------------------------
# Auditoría local: append a archivo, una línea por evento (JSON Lines)
# ---------------------------------------------------------------------------

# Path legacy del audit (v1.0.x escribía acá, en el volumen privado /data del
# add-on). La migración v1.1.0 lo copia una sola vez al path nuevo /config/audit.log.
LEGACY_AUDIT_PATH = Path("/data/audit.log")


class AuditLogger:
    """Escribe TODOS los eventos parseados a un archivo JSON Lines para auditoría.

    Mantiene un handle de archivo persistente en modo append y hace flush()
    explícito tras cada write: ~5x menos syscalls que open/close por evento,
    misma garantía de durabilidad ante crash. Sin rotación todavía (volumen
    estimado ~9 MB/año); cuando llegue (S7-S8) se maneja con patrón SIGHUP.
    """

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        try:
            self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            self._fh.flush()
        except OSError as exc:
            # Nunca bloqueamos el reenvío a HA (camino crítico M1/M2);
            # reportamos a stderr y seguimos.
            logging.getLogger("audit").error("Falló escritura audit log: %s", exc)


def build_audit_record(event: dict) -> dict:
    """Proyecta el evento normalizado de parse_event_block() al schema JSON Lines
    del audit (superset acordado).

    Campos canónicos: received_ts (timestamp de escritura, ISO 8601 tz del Pi),
    door, major, sub, sub_name, serial, raw. Más extras útiles que ya producía
    el parser (incluido device_ts, el dateTime reportado por la controladora).
    Para eventos kind=="other" los canónicos van en null (schema homogéneo).
    """
    received_ts = datetime.now().astimezone().isoformat()  # tz America/Bogota del container

    return {
        "received_ts": received_ts,
        "kind": event.get("kind"),
        "major": event.get("major"),
        "sub": event.get("sub"),
        "sub_name": event.get("description"),   # renombrado
        "door": event.get("door_no"),           # renombrado
        "serial": event.get("serial_no"),       # renombrado
        # extras útiles del parser (superset)
        "device_ts": event.get("timestamp"),    # dateTime reportado por la controladora
        "device_ip": event.get("device_ip"),
        "device_mac": event.get("device_mac"),
        "door_name": event.get("door_name"),
        "card_no": event.get("card_no"),
        "employee_no": event.get("employee_no"),
        "verify_mode": event.get("verify_mode"),
        "event_type": event.get("event_type"),  # presente en kind=="other"
        "raw": event.get("raw"),
    }


def migrate_legacy_audit(legacy: Path, new: Path, log: logging.Logger) -> None:
    """Copia única del audit viejo (/data) al nuevo path (/config) en el arranque.

    - legacy existe y new NO existe   -> copia (copy2) y loguea N líneas migradas.
    - legacy existe y new YA existe    -> NO sobreescribe; loguea warning y sigue.
    - legacy no existe                 -> sigue normal.
    Cualquier fallo (espacio, permisos) -> error a stderr y arranque normal.
    Nunca aborta el arranque. Legacy queda intacto (respaldo hasta v1.2.0).
    """
    try:
        if legacy == new or not legacy.exists():
            return
        if new.exists():
            log.warning(
                "Audit nuevo ya existe en %s; NO se migra el legacy (%s). Continuando.",
                new, legacy,
            )
            return
        new.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, new)
        n = sum(1 for _ in new.open("r", encoding="utf-8", errors="replace"))
        log.info(
            "Migración audit: %d líneas copiadas de %s a %s. Legacy intacto.",
            n, legacy, new,
        )
    except OSError as exc:
        logging.getLogger("audit").error(
            "Falló migración del audit legacy (%s -> %s): %s. "
            "Continuando con el path nuevo.",
            legacy, new, exc,
        )


# ---------------------------------------------------------------------------
# Reenvío a HA via webhook
# ---------------------------------------------------------------------------

def forward_to_ha(webhook_url: str, event: dict, log: logging.Logger) -> None:
    """POST del evento al webhook de HA. No bloquea ante errores."""
    try:
        resp = requests.post(
            webhook_url,
            json=event,
            timeout=5,
            verify=False,  # HA con cert autofirmado en LAN
        )
        if resp.status_code >= 400:
            log.warning(
                "HA webhook respondió %s: %s",
                resp.status_code, resp.text[:200]
            )
    except requests.RequestException as exc:
        log.error("Falló envío al webhook de HA: %s", exc)


# ---------------------------------------------------------------------------
# Loop principal: conecta, escucha, reconecta
# ---------------------------------------------------------------------------

def run(cfg: Config, log: logging.Logger) -> None:
    """Loop principal del listener. Reconecta indefinidamente ante caídas
    y detecta streams zombie (conectado pero sin datos)."""
    migrate_legacy_audit(LEGACY_AUDIT_PATH, cfg.audit_log_path, log)
    audit = AuditLogger(cfg.audit_log_path)
    url = f"http://{cfg.controller_host}/ISAPI/Event/notification/alertStream"

    # Timeout máximo sin recibir nada del stream (incluso heartbeats).
    # Hikvision emite videoloss heartbeats cada ~10s, así que 60s es generoso.
    STREAM_IDLE_TIMEOUT = 60

    while True:
        try:
            log.info("Conectando al stream ISAPI: %s", url)
            response = requests.get(
                url,
                auth=HTTPDigestAuth(cfg.controller_user, cfg.controller_password),
                stream=True,
                timeout=(10, STREAM_IDLE_TIMEOUT),
            )
            response.raise_for_status()
            log.info("Conectado. Escuchando eventos.")

            last_data_at = time.monotonic()

            buffer = b""
            for chunk in response.iter_content(chunk_size=1024):
                # Watchdog: si pasó mucho tiempo sin chunks, asumir zombie
                if time.monotonic() - last_data_at > STREAM_IDLE_TIMEOUT:
                    log.warning(
                        "Stream sin datos durante %ds — asumiendo zombie, "
                        "reconectando.",
                        STREAM_IDLE_TIMEOUT,
                    )
                    break

                if not chunk:
                    continue

                last_data_at = time.monotonic()
                buffer += chunk

                while MIME_BOUNDARY in buffer:
                    part, _, buffer = buffer.partition(MIME_BOUNDARY)
                    if not part.strip():
                        continue

                    if b"\r\n\r\n" in part:
                        _, _, body = part.partition(b"\r\n\r\n")
                    elif b"\n\n" in part:
                        _, _, body = part.partition(b"\n\n")
                    else:
                        continue

                    event = parse_event_block(body, log)
                    if event is None:
                        continue

                    # Audit: siempre todos (Opción C — el filtrado aplica solo a HA)
                    audit.write(build_audit_record(event))

                    # Filtrado para HA
                    if event.get("kind") == "access_controller_event":
                        major = event.get("major")
                        sub = event.get("sub")
                        if should_forward_to_ha(major, sub):
                            log.info(
                                "→ HA: door=%s serial=%s %s",
                                event.get("door_no"),
                                event.get("serial_no"),
                                event.get("description"),
                            )
                            forward_to_ha(cfg.ha_webhook_url, event, log)
                        else:
                            log.debug(
                                "↓ audit-only: %s",
                                event.get("description"),
                            )
                    else:
                        log.debug(
                            "↓ audit-only (other): %s",
                            event.get("event_type"),
                        )

        except requests.exceptions.ReadTimeout:
            log.warning(
                "Read timeout del stream (%ds sin datos). Reconectando.",
                STREAM_IDLE_TIMEOUT,
            )

        except requests.exceptions.HTTPError as exc:
            log.error("HTTP error del controlador: %s", exc)
            if exc.response is not None:
                status = exc.response.status_code
                body = exc.response.text[:500]
                log.error("  Status: %s", status)
                log.error("  Headers WWW-Authenticate: %r",
                          exc.response.headers.get("WWW-Authenticate"))
                log.error("  Body: %r", body)

                if status == 401 and "lockStatus" in body and "lock" in body:
                    import re
                    m = re.search(r"<unlockTime>(\d+)</unlockTime>", body)
                    wait_s = int(m.group(1)) + 30 if m else 900
                    log.error(
                        "*** CUENTA BLOQUEADA por la controladora. "
                        "Esperando %ds antes de reintentar. ***",
                        wait_s,
                    )
                    time.sleep(wait_s)
                    continue

                if status == 401:
                    log.error(
                        "*** Autenticación fallida (401 sin lockout). "
                        "Esperando 5 minutos antes de reintentar. ***"
                    )
                    time.sleep(300)
                    continue

        except requests.exceptions.ConnectionError as exc:
            log.error("Conexión perdida: %s", exc)
        except requests.exceptions.RequestException as exc:
            log.error("Error de request: %s", exc)
        except Exception as exc:
            log.exception("Excepción no manejada en el loop: %s", exc)

        log.info("Reintentando conexión en %ds...", cfg.reconnect_delay)
        time.sleep(cfg.reconnect_delay)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    # Silenciar el warning de cert autofirmado al postear a HA
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return logging.getLogger("hikvision-isapi")


def install_signal_handlers(log: logging.Logger) -> None:
    def handler(signum, _frame):
        log.info("Señal %s recibida, saliendo limpiamente.", signum)
        sys.exit(0)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main() -> None:
    log = setup_logging()
    install_signal_handlers(log)

    try:
        cfg = Config.from_options_json()
    except FileNotFoundError:
        log.error("/data/options.json no existe. ¿Está corriendo dentro del add-on?")
        sys.exit(1)
    except KeyError as exc:
        log.error("Falta opción obligatoria en options.json: %s", exc)
        sys.exit(1)

    log.info(
        "Listener iniciado. Controlador=%s user=%s HA webhook=%s audit=%s",
        cfg.controller_host,
        cfg.controller_user,
        cfg.ha_webhook_url,
        cfg.audit_log_path,
    )
    run(cfg, log)


if __name__ == "__main__":
    main()