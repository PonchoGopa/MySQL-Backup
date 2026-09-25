import subprocess
import gzip
import shutil
from pathlib import Path
from datetime import datetime, timedelta
import time


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

# Días que conservaremos los respaldos
RETENTION_DAYS = 30


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
# RETENCIÓN
# ============================================================

log("-" * 65)
log(
    f"Revisando respaldos con más de "
    f"{RETENTION_DAYS} días..."
)

deleted = clean_old_backups()

log(
    f"Respaldos antiguos eliminados: "
    f"{deleted}"
)

# ============================================================
# LIMPIEZA DE RESPALDOS ANTIGUOS
# ============================================================

log("-" * 65)

if not failed:
    log(f"Revisando respaldos con más de {RETENTION_DAYS} días...")
    deleted = clean_old_backups()
    log(f"Respaldos antiguos eliminados: {deleted}")
else:
    log(
        "No se eliminarán respaldos antiguos porque "
        "el respaldo actual está incompleto."
    )


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


if failed:

    log("ESTADO FINAL: BACKUP INCOMPLETO")

else:

    log(
        "ESTADO FINAL: "
        "BACKUP COMPLETADO CORRECTAMENTE"
    )

log("=" * 65)


# ============================================================
# EXIT CODE
# ============================================================

if failed:
    raise SystemExit(1)

raise SystemExit(0)