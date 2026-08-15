"""Persistencia y dominio de la libreta.

Reglas que no se negocian (ver design.md):
- Los montos son enteros de la unidad mínima.
- `pagos` es append-only: los triggers impiden UPDATE y DELETE.
- Saldo, estado de cobro y vencimiento se derivan al consultar; no se guardan.
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "libreta.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS facturas (
    id         INTEGER PRIMARY KEY,
    cliente    TEXT    NOT NULL,
    monto      INTEGER NOT NULL CHECK (monto > 0),
    fecha_venc TEXT    NOT NULL,
    creada_en  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pagos (
    id            INTEGER PRIMARY KEY,
    factura_id    INTEGER NOT NULL REFERENCES facturas(id),
    monto         INTEGER NOT NULL CHECK (monto <> 0),
    fecha         TEXT    NOT NULL,
    origen        TEXT    NOT NULL DEFAULT 'manual',
    registrado_en TEXT    NOT NULL,
    revierte_a    INTEGER REFERENCES pagos(id)
);

CREATE INDEX IF NOT EXISTS idx_pagos_factura ON pagos(factura_id);

-- Un pago se revierte una sola vez (SQLite admite varios NULL en un UNIQUE).
CREATE UNIQUE INDEX IF NOT EXISTS idx_pagos_revierte ON pagos(revierte_a);

CREATE TRIGGER IF NOT EXISTS pagos_no_update BEFORE UPDATE ON pagos
    BEGIN SELECT RAISE(ABORT, 'movimientos inmutables'); END;

CREATE TRIGGER IF NOT EXISTS pagos_no_delete BEFORE DELETE ON pagos
    BEGIN SELECT RAISE(ABORT, 'movimientos inmutables'); END;

CREATE TABLE IF NOT EXISTS links_pago (
    id             INTEGER PRIMARY KEY,
    factura_id     INTEGER NOT NULL REFERENCES facturas(id),
    proveedor      TEXT    NOT NULL,
    referencia     TEXT    NOT NULL,
    preferencia_id TEXT    NOT NULL,
    url            TEXT    NOT NULL,
    monto          INTEGER NOT NULL,
    expira_en      TEXT    NOT NULL,
    creado_en      TEXT    NOT NULL,
    cancelado_en   TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_links_ref ON links_pago(proveedor, referencia);
CREATE INDEX IF NOT EXISTS idx_links_factura ON links_pago(factura_id);

-- Bandeja de excepciones: todo aviso entra acá antes de interpretarse, y los
-- hallazgos sin factura de una reconciliación entran con fuente distinta.
CREATE TABLE IF NOT EXISTS eventos_webhook (
    id             INTEGER PRIMARY KEY,
    proveedor      TEXT    NOT NULL,
    aviso_id       TEXT    NOT NULL,
    fuente         TEXT    NOT NULL DEFAULT 'webhook',
    cuerpo_crudo   TEXT    NOT NULL,
    firma_valida   INTEGER NOT NULL,
    recibido_en    TEXT    NOT NULL,
    procesado_en   TEXT,
    motivo_no_proc TEXT,
    pago_id        INTEGER REFERENCES pagos(id),
    pago_externo_id TEXT,
    firma          TEXT,
    request_id     TEXT,
    comision       INTEGER,
    neto           INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_eventos_aviso
    ON eventos_webhook(proveedor, aviso_id);

CREATE TABLE IF NOT EXISTS reconciliaciones (
    id           INTEGER PRIMARY KEY,
    desde        TEXT    NOT NULL,
    hasta        TEXT    NOT NULL,
    ejecutada_en TEXT    NOT NULL,
    consultados  INTEGER NOT NULL DEFAULT 0,
    registrados  INTEGER NOT NULL DEFAULT 0,
    excepciones  INTEGER NOT NULL DEFAULT 0
);

"""

# Las vistas se crean después de migrar las columnas, porque las usan. Se
# recrean siempre: son derivadas, y CREATE VIEW IF NOT EXISTS dejaría una base
# de la fase 1 con la definición vieja.
SCHEMA_VISTAS = """
DROP VIEW IF EXISTS facturas_estado;
DROP VIEW IF EXISTS facturas_saldo;

CREATE VIEW facturas_saldo AS
SELECT f.id, f.cliente, f.monto, f.fecha_venc, f.creada_en, f.cobro_por_link,
       f.monto - COALESCE(pg.total, 0) AS saldo
FROM facturas f
LEFT JOIN (SELECT factura_id, SUM(monto) AS total FROM pagos GROUP BY factura_id) pg
       ON pg.factura_id = f.id;

CREATE VIEW facturas_estado AS
SELECT id, cliente, monto, fecha_venc, creada_en, cobro_por_link, saldo,
       CASE WHEN saldo <= 0    THEN 'PAGADA'
            WHEN saldo < monto THEN 'PARCIAL'
            ELSE                    'PENDIENTE' END AS estado,
       CASE WHEN saldo > 0 AND fecha_venc < date('now', 'localtime')
            THEN 1 ELSE 0 END AS vencida,
       CAST(julianday(date('now', 'localtime')) - julianday(fecha_venc) AS INTEGER)
            AS dias_atraso
FROM facturas_saldo;

DROP VIEW IF EXISTS links_estado;

-- De los cuatro estados del link solo se almacena la cancelación: es una
-- decisión humana. Los otros tres se deducen.
CREATE VIEW links_estado AS
SELECT l.*,
       CASE WHEN l.cancelado_en IS NOT NULL THEN 'cancelado'
            WHEN EXISTS (SELECT 1 FROM pagos p
                          WHERE p.link_id = l.id AND p.monto > 0) THEN 'pagado'
            -- datetime() normaliza: el ISO usa 'T' y SQLite un espacio, y
            -- comparados como texto 'T' > ' ' invertiría el resultado.
            WHEN datetime(l.expira_en) < datetime('now', 'localtime') THEN 'expirado'
            ELSE 'vigente' END AS estado
FROM links_pago l;
"""

# Columnas agregadas sobre tablas que ya existen. CREATE TABLE IF NOT EXISTS no
# altera una tabla ya creada, así que una base de la fase 1 se migra por acá.
COLUMNAS_NUEVAS = [
    ("facturas", "cobro_por_link", "INTEGER NOT NULL DEFAULT 0"),
    ("pagos", "proveedor", "TEXT"),
    ("pagos", "referencia_externa", "TEXT"),
    ("pagos", "link_id", "INTEGER REFERENCES links_pago(id)"),
    ("pagos", "reconciliacion_id", "INTEGER REFERENCES reconciliaciones(id)"),
    ("eventos_webhook", "firma", "TEXT"),
    ("eventos_webhook", "request_id", "TEXT"),
]

# Un pago externo se registra una sola vez, venga por webhook o por reconciliación.
# SQLite admite varios NULL, así que los movimientos manuales no estorban.
INDICE_REFERENCIA = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_pagos_ref"
    " ON pagos(proveedor, referencia_externa)"
)


def conectar(path=DB_PATH) -> sqlite3.Connection:
    # ponytail: una conexión compartida entre los hilos de FastAPI; alcanza para
    # un solo escritor. Si alguna vez hay concurrencia real, una conexión por request.
    con = sqlite3.connect(path, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")  # SQLite las ignora por defecto
    # Ahora hay tres caminos que escriben: webhook, procesamiento diferido y
    # reconciliación. WAL permite lectores durante una escritura; el timeout
    # absorbe los solapamientos cortos.
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def _migrar(con: sqlite3.Connection) -> None:
    for tabla, columna, tipo in COLUMNAS_NUEVAS:
        existentes = {f["name"] for f in con.execute(f"PRAGMA table_info({tabla})")}
        if columna not in existentes:
            con.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {tipo}")
    con.execute(INDICE_REFERENCIA)


def inicializar(con: sqlite3.Connection) -> sqlite3.Connection:
    con.executescript(SCHEMA)
    _migrar(con)
    con.executescript(SCHEMA_VISTAS)
    con.commit()
    return con


def _hoy() -> str:
    return date.today().isoformat()


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _fecha_valida(valor, campo: str) -> str:
    s = (valor or "").strip()
    if not s:
        raise ValueError(f"{campo} es obligatoria")
    try:
        return date.fromisoformat(s).isoformat()
    except ValueError:
        raise ValueError(f"{campo} debe tener formato AAAA-MM-DD")


# --- escritura -------------------------------------------------------------


def crear_factura(con, cliente: str, monto: int, fecha_venc: str) -> int:
    cliente = (cliente or "").strip()
    if not cliente:
        raise ValueError("El cliente es obligatorio")
    if monto is None:
        raise ValueError("El monto es obligatorio")
    if monto <= 0:
        raise ValueError("El monto debe ser mayor a cero")
    fecha_venc = _fecha_valida(fecha_venc, "La fecha de vencimiento")
    cur = con.execute(
        "INSERT INTO facturas (cliente, monto, fecha_venc, creada_en) VALUES (?,?,?,?)",
        (cliente, int(monto), fecha_venc, _ahora()),
    )
    con.commit()
    return cur.lastrowid


def registrar_pago(
    con,
    factura_id,
    monto,
    fecha=None,
    origen="manual",
    revierte_a=None,
    proveedor=None,
    referencia_externa=None,
    link_id=None,
    reconciliacion_id=None,
) -> int:
    """Único punto de escritura sobre `pagos`, para todo origen.

    Con `proveedor` y `referencia_externa` el registro es idempotente: si ese
    pago externo ya existe devuelve el movimiento que ya estaba, sin crear otro.
    """
    if monto is None or int(monto) == 0:
        raise ValueError("El monto del pago debe ser distinto de cero")
    factura = con.execute(
        "SELECT cobro_por_link FROM facturas WHERE id = ?", (factura_id,)
    ).fetchone()
    if factura is None:
        raise ValueError("La factura no existe")
    if origen == "manual" and factura["cobro_por_link"]:
        raise ValueError(
            "El cobro de esta factura es por link; apaga el cobro por link para "
            "registrar un pago manual"
        )
    fecha = _fecha_valida(fecha or _hoy(), "La fecha del pago")
    try:
        cur = con.execute(
            "INSERT INTO pagos (factura_id, monto, fecha, origen, registrado_en,"
            " revierte_a, proveedor, referencia_externa, link_id, reconciliacion_id)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                factura_id, int(monto), fecha, origen, _ahora(), revierte_a,
                proveedor, referencia_externa, link_id, reconciliacion_id,
            ),
        )
    except sqlite3.IntegrityError:
        # La unicidad de (proveedor, referencia_externa) es la que cuida el
        # dinero: el segundo intento no escribe y devuelve el movimiento vigente.
        ya = con.execute(
            "SELECT id FROM pagos WHERE proveedor = ? AND referencia_externa = ?",
            (proveedor, referencia_externa),
        ).fetchone()
        if ya is None:
            raise
        return ya["id"]
    con.commit()
    return cur.lastrowid


def revertir_pago(con, pago_id: int) -> int:
    pago = con.execute("SELECT * FROM pagos WHERE id = ?", (pago_id,)).fetchone()
    if pago is None:
        raise ValueError("El movimiento no existe")
    if pago["revierte_a"] is not None:
        raise ValueError("Una reversión no se revierte")
    if pago["proveedor"] is not None:
        # Un movimiento del proveedor se corrige con un reembolso suyo, que llega
        # como movimiento negativo propio. Revertirlo acá desincronizaría el saldo.
        raise ValueError("Un movimiento del proveedor se corrige con un reembolso")
    ya = con.execute("SELECT 1 FROM pagos WHERE revierte_a = ?", (pago_id,)).fetchone()
    if ya is not None:
        raise ValueError("Ese movimiento ya fue revertido")
    return registrar_pago(
        con, pago["factura_id"], -pago["monto"], fecha=_hoy(), revierte_a=pago_id
    )


# --- consultas -------------------------------------------------------------


def listar_facturas(con):
    return con.execute(
        "SELECT * FROM facturas_estado ORDER BY vencida DESC, fecha_venc, id"
    ).fetchall()


def obtener_factura(con, factura_id):
    return con.execute("SELECT * FROM facturas_estado WHERE id = ?", (factura_id,)).fetchone()


def historial(con, factura_id):
    """Movimientos en orden cronológico, marcando cuáles ya fueron revertidos."""
    return con.execute(
        "SELECT p.*, (SELECT 1 FROM pagos r WHERE r.revierte_a = p.id) AS revertido"
        " FROM pagos p WHERE p.factura_id = ? ORDER BY p.id",
        (factura_id,),
    ).fetchall()


# --- links de pago ---------------------------------------------------------


def set_cobro_por_link(con, factura_id: int, activo: bool) -> None:
    con.execute(
        "UPDATE facturas SET cobro_por_link = ? WHERE id = ?",
        (1 if activo else 0, factura_id),
    )
    con.commit()


def crear_link(con, factura_id, proveedor, referencia, preferencia_id, url, monto, expira_en) -> int:
    cur = con.execute(
        "INSERT INTO links_pago (factura_id, proveedor, referencia, preferencia_id,"
        " url, monto, expira_en, creado_en) VALUES (?,?,?,?,?,?,?,?)",
        (factura_id, proveedor, referencia, preferencia_id, url, int(monto),
         expira_en, _ahora()),
    )
    con.commit()
    return cur.lastrowid


def cancelar_link(con, link_id: int) -> None:
    con.execute(
        "UPDATE links_pago SET cancelado_en = ? WHERE id = ? AND cancelado_en IS NULL",
        (_ahora(), link_id),
    )
    con.commit()


def link_vigente(con, factura_id: int):
    return con.execute(
        "SELECT * FROM links_estado WHERE factura_id = ? AND estado = 'vigente'"
        " ORDER BY id DESC LIMIT 1",
        (factura_id,),
    ).fetchone()


def links_de_factura(con, factura_id: int):
    return con.execute(
        "SELECT * FROM links_estado WHERE factura_id = ? ORDER BY id DESC",
        (factura_id,),
    ).fetchall()


def link_por_referencia(con, proveedor: str, referencia: str):
    return con.execute(
        "SELECT * FROM links_estado WHERE proveedor = ? AND referencia = ?",
        (proveedor, referencia),
    ).fetchone()


# --- avisos recibidos ------------------------------------------------------


def registrar_evento(con, proveedor, aviso_id, cuerpo_crudo, firma_valida,
                     fuente="webhook", firma="", request_id=""):
    """Persiste el aviso íntegro. Devuelve None si ese aviso ya estaba registrado."""
    cur = con.execute(
        "INSERT INTO eventos_webhook (proveedor, aviso_id, fuente, cuerpo_crudo,"
        " firma_valida, recibido_en, firma, request_id) VALUES (?,?,?,?,?,?,?,?)"
        " ON CONFLICT (proveedor, aviso_id) DO NOTHING",
        (proveedor, str(aviso_id), fuente, cuerpo_crudo, 1 if firma_valida else 0,
         _ahora(), firma, request_id),
    )
    con.commit()
    return cur.lastrowid if cur.rowcount else None


def cerrar_evento(con, evento_id, motivo=None, pago_id=None, comision=None, neto=None,
                  pago_externo_id=None) -> None:
    """Marca el aviso como procesado. `motivo` presente = no produjo movimiento."""
    con.execute(
        "UPDATE eventos_webhook SET procesado_en = ?, motivo_no_proc = ?, pago_id = ?,"
        " comision = ?, neto = ?, pago_externo_id = COALESCE(?, pago_externo_id)"
        " WHERE id = ?",
        (_ahora(), motivo, pago_id, comision, neto, pago_externo_id, evento_id),
    )
    con.commit()


def obtener_evento(con, evento_id):
    return con.execute("SELECT * FROM eventos_webhook WHERE id = ?", (evento_id,)).fetchone()


def excepciones(con):
    """Avisos que no produjeron movimiento y cuyo pago sigue sin registrarse.

    Un aviso que falló pero cuyo pago entró después por otra vía deja de figurar:
    ya no es una excepción pendiente.
    """
    return con.execute(
        "SELECT * FROM eventos_webhook e WHERE e.pago_id IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM pagos p WHERE p.proveedor = e.proveedor"
        "                  AND p.referencia_externa = e.pago_externo_id)"
        " ORDER BY e.recibido_en DESC, e.id DESC"
    ).fetchall()


# --- reconciliaciones ------------------------------------------------------


def crear_reconciliacion(con, desde: str, hasta: str) -> int:
    cur = con.execute(
        "INSERT INTO reconciliaciones (desde, hasta, ejecutada_en) VALUES (?,?,?)",
        (desde, hasta, _ahora()),
    )
    con.commit()
    return cur.lastrowid


def cerrar_reconciliacion(con, rec_id, consultados, registrados, excepciones) -> None:
    con.execute(
        "UPDATE reconciliaciones SET consultados = ?, registrados = ?, excepciones = ?"
        " WHERE id = ?",
        (consultados, registrados, excepciones, rec_id),
    )
    con.commit()


def obtener_reconciliacion(con, rec_id):
    return con.execute("SELECT * FROM reconciliaciones WHERE id = ?", (rec_id,)).fetchone()


def agregados(con):
    fila = con.execute(
        "SELECT COALESCE(SUM(CASE WHEN saldo > 0 THEN saldo END), 0) AS adeudado,"
        "       COALESCE(SUM(CASE WHEN vencida = 1 THEN saldo END), 0) AS vencido"
        " FROM facturas_estado"
    ).fetchone()
    por_cliente = con.execute(
        "SELECT cliente, SUM(saldo) AS deuda FROM facturas_estado"
        " WHERE saldo > 0 GROUP BY cliente ORDER BY deuda DESC"
    ).fetchall()
    return {
        "adeudado": fila["adeudado"],
        "vencido": fila["vencido"],
        "por_cliente": por_cliente,
    }
