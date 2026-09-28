import subprocess
import gzip
import shutil
from pathlib import Path
from datetime import datetime, timedelta
import time
import os
import json
from urllib.request import Request, urlopen
from urllib.parse import urlencode, quote
from urllib.error import HTTPError, URLError


# ============================================================
# CONFIGURACIÓN
# ============================================================

MYSQLDUMP = Path(
    r"C:\Program Files\MySQL\MySQL Server 8.0\bin\mysqldump.exe"
)

LOGIN_PATH = "backup"

DATABASES = [
    "dollar_price",
    "kimexfinances",
    "kimexlogistics",
    "kimexmtto",
    "kimexproduction",
    "kimexquality",
    "kimexrh",
    "plex_data",
    "plex_template",
    "production_db",
]

BASE_DIR = Path(r"C:\MySQL_Backup")
BACKUP_DIR = BASE_DIR / "backups"
LOG_DIR = BASE_DIR / "logs"
ONEDRIVE_BACKUP_DIR = Path(
    r"C:\Users\IT-D\OneDrive - KI USA MEX\Documentos IT\Backups"
)

# Días que conservaremos los respaldos
RETENTION_DAYS = 30

# Microsoft Graph: configurar estas variables en el entorno de la cuenta
# que ejecuta la tarea programada. Nunca escribir secretos en este archivo.
# MYSQL_BACKUP_TENANT_ID, MYSQL_BACKUP_CLIENT_ID, MYSQL_BACKUP_CLIENT_SECRET
EMAIL_SENDER = "sistemas@kiusamex.mx"
EMAIL_RECIPIENT = "sistemas@kiusamex.mx"
EMAIL_TIMEOUT_SECONDS = 30


# ============================================================
# FUNCIONES
# ============================================================

def log(message):
    """Muestra y guarda mensajes del proceso."""

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {message}"

    print(line)

    with open(
        LOG_DIR / "backup.log",
        "a",
        encoding="utf-8"
    ) as f:
        f.write(line + "\n")


def format_size(bytes_size):
    """Convierte bytes a MB."""

    return bytes_size / (1024 * 1024)


def send_confirmation(subject, body):
    """Solicita el envío a Graph; HTTP 202 confirma aceptación, no entrega."""
    names = (
        "MYSQL_BACKUP_TENANT_ID",
        "MYSQL_BACKUP_CLIENT_ID",
        "MYSQL_BACKUP_CLIENT_SECRET",
    )
    values = [os.environ.get(name, "") for name in names]
    missing = [name for name, value in zip(names, values) if not value.strip()]
    if missing:
        raise RuntimeError("Falta configurar: " + ", ".join(missing))
    tenant, client, secret = values
    token_request = Request(
        "https://login.microsoftonline.com/"
        + quote(tenant.strip(), safe="") + "/oauth2/v2.0/token",
        data=urlencode({
            "client_id": client.strip(),
            "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urlopen(token_request, timeout=EMAIL_TIMEOUT_SECONDS) as response:
            token = json.load(response).get("access_token")
    except HTTPError as e:
        raise RuntimeError(
            f"Autenticación Microsoft 365 rechazada (HTTP {e.code}); "
            "revisar tenant, aplicación y vigencia del secreto."
        ) from None
    except (URLError, OSError, ValueError):
        raise RuntimeError("No fue posible obtener un token de Microsoft 365.") from None
    if not isinstance(token, str) or not token:
        raise RuntimeError("Microsoft 365 no devolvió un token de acceso.")

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": EMAIL_RECIPIENT}}],
        },
        "saveToSentItems": True,
    }
    request = Request(
        "https://graph.microsoft.com/v1.0/users/"
        + quote(EMAIL_SENDER, safe="") + "/sendMail",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json; charset=utf-8",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=EMAIL_TIMEOUT_SECONDS) as response:
            if response.status != 202:
                raise RuntimeError(f"Respuesta inesperada de Graph: HTTP {response.status}.")
    except HTTPError as e:
        raise RuntimeError(
            f"Graph rechazó el correo (HTTP {e.code}); revisar permiso Mail.Send y buzón."
        ) from None
    except (URLError, OSError):
        # No reintentar automáticamente: un timeout puede ocurrir después
        # de que Graph haya aceptado el mensaje y producir duplicados.
        raise RuntimeError(
            "No se pudo confirmar la aceptación del correo; revisar red y Elementos enviados."
        ) from None


def compress_file(source_file, compressed_file):
    """Comprime un archivo SQL utilizando gzip."""

    with open(source_file, "rb") as source:
        with gzip.open(compressed_file, "wb") as target:
            shutil.copyfileobj(source, target)


def clean_old_backups():
    """Elimina carpetas de respaldo mayores a RETENTION_DAYS."""

    limit = datetime.now() - timedelta(days=RETENTION_DAYS)

    deleted = 0

    for folder in BACKUP_DIR.iterdir():

        if not folder.is_dir():
            continue

        try:

            folder_date = datetime.strptime(
                folder.name,
                "%Y-%m-%d_%H-%M-%S"
            )

            if folder_date < limit:

                shutil.rmtree(folder)

                log(
                    f"Eliminado backup antiguo: "
                    f"{folder.name}"
                )

                deleted += 1

        except ValueError:
            # Ignorar carpetas que no pertenezcan
            # al sistema de backup.
            continue

    return deleted


# ============================================================
# PREPARACIÓN
# ============================================================

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

if not MYSQLDUMP.exists():

    print("ERROR: No se encontró mysqldump:")
    print(MYSQLDUMP)

    raise SystemExit(1)


# ============================================================
# CREAR CARPETA DEL RESPALDO
# ============================================================

backup_start = datetime.now()

backup_date = backup_start.strftime(
    "%Y-%m-%d_%H-%M-%S"
)

current_backup_dir = (
    BACKUP_DIR / backup_date
)

current_backup_dir.mkdir(
    parents=True,
    exist_ok=True
)


# ============================================================
# INICIO
# ============================================================

log("=" * 65)
log("INICIO DE RESPALDO MYSQL")
log(f"Login Path: {LOGIN_PATH}")
log(f"Destino: {current_backup_dir}")
log(f"Bases a respaldar: {len(DATABASES)}")
log("=" * 65)

successful = []
failed = []

total_original_size = 0
total_compressed_size = 0


# ============================================================
# RESPALDAR BASES
# ============================================================

for database in DATABASES:

    database_start = time.time()

    log(f"Iniciando respaldo: {database}")

    sql_file = (
        current_backup_dir /
        f"{database}.sql"
    )

    gzip_file = (
        current_backup_dir /
        f"{database}.sql.gz"
    )

    command = [
        str(MYSQLDUMP),

        f"--login-path={LOGIN_PATH}",

        "--single-transaction",
        "--quick",
        "--skip-lock-tables",

        "--routines",
        "--events",
        "--triggers",

        "--no-tablespaces",

        "--default-character-set=utf8mb4",

        database,
    ]

    try:

        # ----------------------------------------------------
        # MYSQLDUMP
        # ----------------------------------------------------

        with open(sql_file, "wb") as output:

            result = subprocess.run(
                command,
                stdout=output,
                stderr=subprocess.PIPE
            )

        if result.returncode != 0:

            error = result.stderr.decode(
                "utf-8",
                errors="replace"
            ).strip()

            failed.append(database)

            log(f"ERROR en {database}:")
            log(error)

            sql_file.unlink(
                missing_ok=True
            )

            continue

        # ----------------------------------------------------
        # VALIDAR ARCHIVO
        # ----------------------------------------------------

        original_size = sql_file.stat().st_size

        if original_size == 0:

            failed.append(database)

            log(
                f"ERROR: {database} "
                "generó un archivo vacío"
            )

            sql_file.unlink(
                missing_ok=True
            )

            continue

        # ----------------------------------------------------
        # COMPRIMIR
        # ----------------------------------------------------

        compress_file(
            sql_file,
            gzip_file
        )

        compressed_size = (
            gzip_file.stat().st_size
        )

        # ----------------------------------------------------
        # VALIDAR COMPRESIÓN
        # ----------------------------------------------------

        if compressed_size == 0:

            failed.append(database)

            log(
                f"ERROR: La compresión de "
                f"{database} generó un archivo vacío"
            )

            gzip_file.unlink(
                missing_ok=True
            )

            continue

        # ----------------------------------------------------
        # ELIMINAR SQL SIN COMPRIMIR
        # ----------------------------------------------------

        sql_file.unlink()

        # ----------------------------------------------------
        # RESULTADO
        # ----------------------------------------------------

        total_original_size += original_size
        total_compressed_size += compressed_size

        elapsed = (
            time.time() - database_start
        )

        successful.append(database)

        log(
            f"OK: {database} | "
            f"{format_size(original_size):.2f} MB -> "
            f"{format_size(compressed_size):.2f} MB | "
            f"{elapsed:.1f} segundos"
        )

    except Exception as e:

        failed.append(database)

        log(
            f"ERROR inesperado en "
            f"{database}: {e}"
        )

        sql_file.unlink(
            missing_ok=True
        )

        gzip_file.unlink(
            missing_ok=True
        )


# ============================================================
# COPIA SECUNDARIA Y RETENCIÓN LOCAL
# ============================================================

local_complete = not failed and len(successful) == len(DATABASES) == 10
secondary_complete = False
retention_failed = False

log("-" * 65)

if local_complete:
    secondary_dir = ONEDRIVE_BACKUP_DIR / backup_date
    log(f"INICIO de copia secundaria a OneDrive: {secondary_dir}")
    try:
        # No mezclar esta ejecución con una carpeta que ya exista.
        ONEDRIVE_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copytree(current_backup_dir, secondary_dir)

        # Confirmar que los diez archivos copiados existen y tienen
        # el mismo tamaño que los originales antes de permitir retención.
        for database in DATABASES:
            source = current_backup_dir / f"{database}.sql.gz"
            target = secondary_dir / source.name
            if not target.is_file() or target.stat().st_size != source.stat().st_size:
                raise OSError(f"Copia incompleta o tamaño incorrecto: {target}")

        secondary_complete = True
        log(f"ÉXITO de copia secundaria a OneDrive: {secondary_dir}")
        # Esto confirma la copia a la carpeta local de OneDrive.
        # La sincronización a la nube la realiza el cliente OneDrive.
    except Exception as e:
        log(f"ERROR en copia secundaria a OneDrive: {e}")
        log("La carpeta secundaria puede estar incompleta; se conserva para revisión.")
else:
    log("Copia secundaria omitida: no se completaron correctamente las 10 bases.")

if local_complete and secondary_complete:
    try:
        log(f"Revisando respaldos locales con más de {RETENTION_DAYS} días...")
        deleted = clean_old_backups()
        log(f"Respaldos locales antiguos eliminados: {deleted}")
    except Exception as e:
        retention_failed = True
        log(f"ERROR en retención local: {e}")
else:
    log(
        "No se eliminarán respaldos locales antiguos porque "
        "el respaldo actual o su copia secundaria está incompleto."
    )

backup_complete = local_complete and secondary_complete and not retention_failed


# ============================================================
# RESULTADO FINAL
# ============================================================

backup_end = datetime.now()

duration = (
    backup_end - backup_start
).total_seconds()

log("=" * 65)

log(
    f"RESULTADO: "
    f"{len(successful)}/{len(DATABASES)} "
    "bases respaldadas correctamente"
)

if successful:

    log(
        "Correctas: "
        + ", ".join(successful)
    )

if failed:

    log(
        "Fallidas: "
        + ", ".join(failed)
    )


log(
    f"Tamaño original total: "
    f"{format_size(total_original_size):.2f} MB"
)

log(
    f"Tamaño comprimido total: "
    f"{format_size(total_compressed_size):.2f} MB"
)

if total_original_size > 0:

    reduction = (
        1 -
        (
            total_compressed_size /
            total_original_size
        )
    ) * 100

    log(
        f"Reducción por compresión: "
        f"{reduction:.1f}%"
    )


log(
    f"Duración total: "
    f"{duration:.1f} segundos"
)


if not backup_complete:

    log("ESTADO FINAL: BACKUP INCOMPLETO")

else:

    log(
        "ESTADO FINAL: "
        "BACKUP COMPLETADO CORRECTAMENTE"
    )

log("=" * 65)


# ============================================================
# CORREO DE CONFIRMACIÓN
# ============================================================

notification_failed = False
if local_complete and secondary_complete:
    log(f"INICIO de correo de confirmación: {EMAIL_RECIPIENT}")
    retention_status = (
        "ERROR: revisar backup.log."
        if retention_failed else f"Correcta ({RETENTION_DAYS} días)."
    )
    subject = f"[MySQL Backup] Dos copias completadas - {backup_date}"
    body = (
        "Se completaron correctamente el respaldo local y la copia secundaria.\n\n"
        f"Inicio: {backup_start:%Y-%m-%d %H:%M:%S}\n"
        f"Fin: {backup_end:%Y-%m-%d %H:%M:%S}\n"
        f"Bases respaldadas: {len(successful)}/{len(DATABASES)}\n"
        f"Bases: {', '.join(successful)}\n\n"
        f"Respaldo local: {current_backup_dir}\n"
        f"Copia secundaria: {secondary_dir}\n\n"
        f"Tamaño comprimido: {format_size(total_compressed_size):.2f} MB\n"
        f"Duración del proceso antes del correo: {duration:.1f} segundos\n"
        f"Retención local: {retention_status}\n\n"
        "La copia secundaria se verificó en la carpeta local de OneDrive. "
        "Este aviso no confirma la sincronización a la nube; "
        "esa operación depende del cliente OneDrive.\n"
    )
    try:
        send_confirmation(subject, body)
        log("ÉXITO: Microsoft 365 aceptó el correo para envío (HTTP 202).")
    except Exception as e:
        notification_failed = True
        log(f"ERROR en correo de confirmación: {e}")
        log("Los respaldos se conservaron; la tarea terminará con código 1 por el aviso fallido.")
else:
    log("Correo de confirmación omitido: no se completaron ambos respaldos.")

log(f"RESULTADO DE LA TAREA: código {1 if not backup_complete or notification_failed else 0}")


# ============================================================
# EXIT CODE
# ============================================================

if not backup_complete or notification_failed:
    raise SystemExit(1)

raise SystemExit(0)
