"""Cobro automático: links, avisos del proveedor y conciliación.

Todo lo que escribe dinero pasa por `db.registrar_pago`. Acá vive lo que ocurre
antes de esa llamada: generar el link, recibir el aviso, consultar el pago real
y decidir si corresponde un movimiento.
"""

import json
from datetime import date, timedelta

from app import db
from app.mercadopago import PROVEEDOR, ErrorProveedor, PagoProveedor

MOTIVO_FIRMA = "firma inválida o ausente"
MOTIVO_ILEGIBLE = "aviso ilegible"
MOTIVO_NO_PAGO = "el aviso no se refiere a un pago"
MOTIVO_PROVEEDOR = "no se pudo consultar el pago al proveedor"
MOTIVO_REFERENCIA = "referencia desconocida: no corresponde a ningún link"
MOTIVO_SIN_FACTURA = "el link no corresponde a ninguna factura vigente"
MOTIVO_HUERFANO = "pago sin factura asociable"


# --- links de pago ---------------------------------------------------------


def _referencia(con, factura_id: int) -> str:
    previos = con.execute(
        "SELECT COUNT(*) AS n FROM links_pago WHERE factura_id = ?", (factura_id,)
    ).fetchone()["n"]
    return f"fac-{factura_id}-{previos + 1}"


def generar_link(con, cliente, factura_id: int):
    """Crea la preferencia en el proveedor y persiste el link.

    Si el proveedor falla no se persiste nada y el modo de cobro no cambia.
    """
    factura = db.obtener_factura(con, factura_id)
    if factura is None:
        raise ValueError("La factura no existe")
    if factura["saldo"] <= 0:
        raise ValueError("La factura no tiene saldo pendiente")

    referencia = _referencia(con, factura_id)
    link = cliente.crear_preferencia(
        referencia=referencia,
        monto=factura["saldo"],
        descripcion=f"Factura #{factura_id} - {factura['cliente']}",
    )
    vigente = db.link_vigente(con, factura_id)
    if vigente is not None:
        db.cancelar_link(con, vigente["id"])  # a lo sumo un link vigente
    link_id = db.crear_link(
        con, factura_id, PROVEEDOR, referencia, link.preferencia_id,
        link.url, factura["saldo"], link.expira_en,
    )
    return db.link_por_referencia(con, PROVEEDOR, referencia) if link_id else None


def link_para_factura(con, cliente, factura_id: int):
    """Devuelve el link vigente; si expiró y la factura cobra por link, genera otro.

    La regeneración es perezosa: ocurre cuando alguien pide el link, no en un
    proceso periódico.
    """
    factura = db.obtener_factura(con, factura_id)
    if factura is None:
        raise ValueError("La factura no existe")
    vigente = db.link_vigente(con, factura_id)
    if vigente is not None:
        return vigente
    if not factura["cobro_por_link"]:
        return None
    return generar_link(con, cliente, factura_id)


def encender_cobro_por_link(con, cliente, factura_id: int):
    """Enciende el modo y deja un link vigente. Si el proveedor falla, no cambia nada."""
    link = generar_link(con, cliente, factura_id)
    db.set_cobro_por_link(con, factura_id, True)
    return link


def apagar_cobro_por_link(con, cliente, factura_id: int) -> None:
    """Puerta de salida: cancela el link vigente y rehabilita el pago manual."""
    vigente = db.link_vigente(con, factura_id)
    if vigente is not None:
        try:
            cliente.cancelar_preferencia(vigente["preferencia_id"])
        except ErrorProveedor:
            pass  # el link local queda cancelado igual; el remoto expira solo
        db.cancelar_link(con, vigente["id"])
    db.set_cobro_por_link(con, factura_id, False)


# --- recepción de avisos ---------------------------------------------------


def recibir_aviso(con, cuerpo_crudo: bytes, aviso_id: str, firma_ok: bool,
                  firma: str = "", request_id: str = ""):
    """Persiste el aviso íntegro. Devuelve el id del evento, o None si ya estaba.

    Guarda también la firma recibida: sin eso, un rechazo por firma es
    indiagnosticable desde la bandeja.
    """
    return db.registrar_evento(
        con, PROVEEDOR, aviso_id,
        cuerpo_crudo.decode("utf-8", errors="replace"),
        firma_ok, firma=firma, request_id=request_id,
    )


def _pago_externo_id(cuerpo_crudo: str) -> str | None:
    """El proveedor manda dos formatos de aviso y solo uno trae `data.id`.

        webhook:  {"type":"payment", "data":{"id":"123"}, "id":9876}
        IPN:      {"topic":"payment", "resource":"123"}

    En el segundo, `id` es el del aviso, no el del pago; el pago está en
    `resource`. Confundirlos consultaría un pago inexistente.
    """
    try:
        cuerpo = json.loads(cuerpo_crudo)
    except (json.JSONDecodeError, TypeError):
        return None
    tipo = cuerpo.get("type") or cuerpo.get("topic")
    if tipo not in (None, "payment"):
        return None  # merchant_order y demás no son pagos
    if "resource" in cuerpo:
        recurso = str(cuerpo["resource"]).rstrip("/")
        return recurso.rsplit("/", 1)[-1] or None
    datos = cuerpo.get("data") or {}
    pago_id = datos.get("id") or (cuerpo.get("id") if "data" not in cuerpo else None)
    return str(pago_id) if pago_id else None


def procesar_evento(con, cliente, evento_id: int) -> None:
    """Consulta el pago real y registra el movimiento si corresponde.

    Nunca toma monto ni estado del cuerpo del aviso: solo el identificador.
    """
    evento = db.obtener_evento(con, evento_id)
    if evento is None or evento["procesado_en"] is not None:
        return
    if not evento["firma_valida"]:
        return db.cerrar_evento(con, evento_id, motivo=MOTIVO_FIRMA)

    pago_id = _pago_externo_id(evento["cuerpo_crudo"])
    if pago_id is None:
        try:
            json.loads(evento["cuerpo_crudo"])
        except (json.JSONDecodeError, TypeError):
            return db.cerrar_evento(con, evento_id, motivo=MOTIVO_ILEGIBLE)
        return db.cerrar_evento(con, evento_id, motivo=MOTIVO_NO_PAGO)

    try:
        pago = cliente.consultar_pago(pago_id)
    except ErrorProveedor:
        return db.cerrar_evento(con, evento_id, motivo=MOTIVO_PROVEEDOR,
                                pago_externo_id=pago_id)

    if not pago.confirmado:
        return db.cerrar_evento(con, evento_id, motivo=f"pago {pago.estado_proveedor}",
                                pago_externo_id=pago.id)

    link = db.link_por_referencia(con, PROVEEDOR, pago.referencia or "")
    if link is None:
        return db.cerrar_evento(con, evento_id, motivo=MOTIVO_REFERENCIA,
                                pago_externo_id=pago.id)
    factura = db.obtener_factura(con, link["factura_id"])
    if factura is None:
        return db.cerrar_evento(con, evento_id, motivo=MOTIVO_SIN_FACTURA,
                                pago_externo_id=pago.id)

    movimiento_id = _registrar_cobro(con, pago, link)
    _registrar_reembolsos(con, pago, link)
    db.cerrar_evento(con, evento_id, pago_id=movimiento_id, comision=pago.comision,
                     neto=pago.neto, pago_externo_id=pago.id)


def _registrar_cobro(con, pago: PagoProveedor, link, reconciliacion_id=None) -> int:
    """Registra el monto bruto. La comisión no toca el saldo de la factura."""
    return db.registrar_pago(
        con,
        link["factura_id"],
        pago.monto,
        origen=PROVEEDOR,
        proveedor=PROVEEDOR,
        referencia_externa=pago.id,
        link_id=link["id"],
        reconciliacion_id=reconciliacion_id,
    )


def _registrar_reembolsos(con, pago: PagoProveedor, link) -> None:
    """Cada reembolso es un movimiento negativo propio, no una reversión.

    El proveedor admite varios reembolsos parciales sobre un mismo pago, cosa
    que `revierte_a` (único por movimiento) no podría representar.
    """
    for reembolso in pago.reembolsos:
        db.registrar_pago(
            con,
            link["factura_id"],
            -abs(reembolso["monto"]),
            origen=PROVEEDOR,
            proveedor=PROVEEDOR,
            referencia_externa=f"{pago.id}:refund:{reembolso['id']}",
            link_id=link["id"],
        )


# --- reconciliación de respaldo --------------------------------------------


def reconciliar(con, cliente, desde: str = None, hasta: str = None, dias: int = 7):
    """Recupera los pagos confirmados que no tienen movimiento.

    Es el único mecanismo automático de reparación cuando un aviso se pierde.
    """
    hasta = hasta or date.today().isoformat()
    desde = desde or (date.fromisoformat(hasta) - timedelta(days=dias)).isoformat()
    rec_id = db.crear_reconciliacion(con, desde, hasta)

    # Barrido por período: encuentra también pagos sin link nuestro (huérfanos).
    pagos = list(cliente.buscar_pagos_confirmados(desde, hasta))  # ErrorProveedor sube
    vistos = {p.id for p in pagos}

    # Y pregunta por cada link cuya factura sigue con saldo: es el camino que
    # de verdad recupera un aviso perdido.
    for ref in _referencias_pendientes(con):
        for pago in cliente.buscar_pagos_por_referencia(ref):
            if pago.id not in vistos:
                pagos.append(pago)
                vistos.add(pago.id)

    registrados = excepciones = 0
    for pago in pagos:
        link = db.link_por_referencia(con, PROVEEDOR, pago.referencia or "")
        if link is None or db.obtener_factura(con, link["factura_id"]) is None:
            _excepcion_huerfana(con, pago)
            excepciones += 1
            continue
        ya = con.execute(
            "SELECT 1 FROM pagos WHERE proveedor = ? AND referencia_externa = ?",
            (PROVEEDOR, pago.id),
        ).fetchone()
        _registrar_cobro(con, pago, link, reconciliacion_id=rec_id)
        _registrar_reembolsos(con, pago, link)
        if ya is None:
            registrados += 1

    db.cerrar_reconciliacion(con, rec_id, len(pagos), registrados, excepciones)
    return db.obtener_reconciliacion(con, rec_id)


def _referencias_pendientes(con):
    """Links de facturas que todavía deben plata."""
    return [r["referencia"] for r in con.execute(
        "SELECT l.referencia FROM links_estado l"
        " JOIN facturas_estado f ON f.id = l.factura_id"
        " WHERE f.saldo > 0 ORDER BY l.id"
    )]


def _excepcion_huerfana(con, pago: PagoProveedor) -> None:
    """Un pago sin factura asociable entra a la misma bandeja que los avisos."""
    cuerpo = json.dumps({
        "id": pago.id, "estado": pago.estado, "monto": pago.monto,
        "referencia": pago.referencia,
    })
    evento_id = db.registrar_evento(
        con, PROVEEDOR, f"rec:{pago.id}", cuerpo, True, fuente="reconciliacion"
    )
    if evento_id is not None:
        db.cerrar_evento(con, evento_id, motivo=MOTIVO_HUERFANO, pago_externo_id=pago.id)
