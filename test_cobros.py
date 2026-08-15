"""Verificación del cobro automático: links, avisos, reembolsos y conciliación.

Usa un doble del proveedor: ningún test toca la red. Lo que interesa verificar
es cómo reacciona el sistema a las respuestas del proveedor, incluidas las
feas, que contra la API real no se pueden provocar a voluntad.

Corre con:  python test_cobros.py
"""

import hashlib
import hmac
import json
from datetime import date, datetime, timedelta

from app import cobros, db
from app.mercadopago import (
    PROVEEDOR, ErrorProveedor, LinkProveedor, PagoProveedor, firma_valida,
)

SECRETO = "secreto-de-prueba"


# --- doble del proveedor ---------------------------------------------------


class ProveedorFalso:
    """Misma interfaz que ClienteMercadoPago, sin red."""

    def __init__(self):
        self.pagos = {}
        self.preferencias = {}
        self.canceladas = []
        self.caido = False
        self.busqueda_por_fecha_vacia = False
        self.horas_vigencia = 24
        self._n = 0

    def crear_preferencia(self, referencia, monto, descripcion):
        if self.caido:
            raise ErrorProveedor("proveedor caído")
        self._n += 1
        expira = datetime.now() + timedelta(hours=self.horas_vigencia)
        pref = LinkProveedor(
            preferencia_id=f"pref-{self._n}",
            url=f"https://pago.test/{referencia}",
            expira_en=expira.isoformat(timespec="seconds"),
        )
        self.preferencias[pref.preferencia_id] = referencia
        return pref

    def cancelar_preferencia(self, preferencia_id):
        if self.caido:
            raise ErrorProveedor("proveedor caído")
        self.canceladas.append(preferencia_id)

    def consultar_pago(self, pago_id):
        if self.caido:
            raise ErrorProveedor("proveedor caído")
        if pago_id not in self.pagos:
            raise ErrorProveedor(f"pago {pago_id} inexistente")
        return self.pagos[pago_id]

    def buscar_pagos_confirmados(self, desde, hasta):
        if self.caido:
            raise ErrorProveedor("proveedor caído")
        if self.busqueda_por_fecha_vacia:  # como el sandbox real
            return []
        return [p for p in self.pagos.values() if p.confirmado]

    def buscar_pagos_por_referencia(self, referencia):
        if self.caido:
            raise ErrorProveedor("proveedor caído")
        return [p for p in self.pagos.values()
                if p.confirmado and p.referencia == referencia]

    # helpers de escenario
    def agregar_pago(self, id, referencia, monto, estado="confirmado", comision=0,
                     neto=None, reembolsos=None):
        self.pagos[id] = PagoProveedor(
            id=id, estado=estado, monto=monto, referencia=referencia,
            comision=comision, neto=neto if neto is not None else monto - comision,
            reembolsos=reembolsos or [],
        )
        return self.pagos[id]


# --- utilidades ------------------------------------------------------------


def _entorno():
    con = db.inicializar(db.conectar(":memory:"))
    return con, ProveedorFalso()


def _factura(con, monto=150000, dias=10, cliente="Cliente A"):
    return db.crear_factura(
        con, cliente, monto, (date.today() + timedelta(days=dias)).isoformat()
    )


def _aviso(pago_id, tipo="payment"):
    return json.dumps({"type": tipo, "action": "payment.updated",
                       "data": {"id": pago_id}}).encode()


def _entregar(con, prov, pago_id, aviso_id=None, firma_ok=True, cuerpo=None):
    """Simula la llegada de un aviso y su procesamiento diferido."""
    cuerpo = cuerpo if cuerpo is not None else _aviso(pago_id)
    evento_id = cobros.recibir_aviso(con, cuerpo, aviso_id or f"av-{pago_id}", firma_ok)
    if evento_id is not None:
        cobros.procesar_evento(con, prov, evento_id)
    return evento_id


def _motivos(con):
    return [e["motivo_no_proc"] for e in db.excepciones(con)]


# --- firma -----------------------------------------------------------------


def _firmar(manifiesto):
    return hmac.new(SECRETO.encode(), manifiesto.encode(), hashlib.sha256).hexdigest()


def test_firma_valida():
    v1 = _firmar("id:99;request-id:req-1;ts:1700000000;")
    assert firma_valida(SECRETO, f"ts=1700000000,v1={v1}", "req-1", "99")


def test_firma_omite_componentes_ausentes():
    """La doc del proveedor: los valores que no vienen se quitan del manifiesto."""
    v1 = _firmar("id:99;ts:1700000000;")
    assert firma_valida(SECRETO, f"ts=1700000000,v1={v1}", "", "99")

    v1 = _firmar("ts:1700000000;")
    assert firma_valida(SECRETO, f"ts=1700000000,v1={v1}", "", "")


def test_firma_con_id_alfanumerico_en_minusculas():
    v1 = _firmar("id:abc-9f;request-id:req-1;ts:1700000000;")
    assert firma_valida(SECRETO, f"ts=1700000000,v1={v1}", "req-1", "ABC-9F")


def test_firma_invalida_o_ausente():
    assert not firma_valida(SECRETO, "ts=1700000000,v1=deadbeef", "req-1", "99")
    assert not firma_valida(SECRETO, "", "req-1", "99")
    assert not firma_valida(SECRETO, "ts=1700000000", "req-1", "99")
    # una firma válida para otro pago no sirve para este
    v1 = _firmar("id:99;request-id:req-1;ts:1700000000;")
    assert not firma_valida(SECRETO, f"ts=1700000000,v1={v1}", "req-1", "100")
    # ni una firmada con otro request-id
    assert not firma_valida(SECRETO, f"ts=1700000000,v1={v1}", "req-2", "99")


# --- recepción de avisos ---------------------------------------------------


def test_aviso_con_firma_invalida_no_mueve_saldo():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)

    _entregar(con, prov, "p1", firma_ok=False)
    assert db.obtener_factura(con, fid)["saldo"] == 150000
    assert cobros.MOTIVO_FIRMA in _motivos(con)


def test_aviso_ilegible_queda_persistido():
    con, prov = _entorno()
    _entregar(con, prov, "x", cuerpo=b"esto no es json")
    assert cobros.MOTIVO_ILEGIBLE in _motivos(con)


def test_aviso_que_no_es_de_pago():
    con, prov = _entorno()
    _entregar(con, prov, "x", cuerpo=_aviso("x", tipo="plan"))
    assert cobros.MOTIVO_NO_PAGO in _motivos(con)


def test_aviso_repetido_no_se_reprocesa():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)

    primero = _entregar(con, prov, "p1", aviso_id="av-1")
    repetido = _entregar(con, prov, "p1", aviso_id="av-1")
    assert primero is not None and repetido is None
    assert len(db.historial(con, fid)) == 1
    assert con.execute("SELECT COUNT(*) FROM eventos_webhook").fetchone()[0] == 1


# --- idempotencia del dinero -----------------------------------------------


def test_dos_avisos_distintos_sobre_el_mismo_pago():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)

    _entregar(con, prov, "p1", aviso_id="av-1")
    _entregar(con, prov, "p1", aviso_id="av-2")

    assert len(db.historial(con, fid)) == 1, "el mismo pago no puede registrarse dos veces"
    assert db.obtener_factura(con, fid)["saldo"] == 0


def test_reconciliacion_no_duplica_lo_ya_registrado():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    _entregar(con, prov, "p1")

    resumen = cobros.reconciliar(con, prov)
    assert resumen["registrados"] == 0
    assert len(db.historial(con, fid)) == 1
    assert db.obtener_factura(con, fid)["saldo"] == 0


# --- estados del pago ------------------------------------------------------


def test_solo_los_confirmados_mueven_el_saldo():
    con, prov = _entorno()
    for i, estado in enumerate(("pendiente", "rechazado", "cancelado")):
        fid = _factura(con)
        link = cobros.encender_cobro_por_link(con, prov, fid)
        prov.agregar_pago(f"p{i}", link["referencia"], 150000, estado=estado)
        _entregar(con, prov, f"p{i}")
        assert db.obtener_factura(con, fid)["saldo"] == 150000, estado
        assert db.obtener_factura(con, fid)["estado"] == "PENDIENTE", estado
    assert sorted(_motivos(con)) == ["pago cancelado", "pago pendiente", "pago rechazado"]


def test_pago_que_se_confirma_despues():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000, estado="pendiente")
    _entregar(con, prov, "p1", aviso_id="av-1")
    assert db.obtener_factura(con, fid)["saldo"] == 150000

    prov.agregar_pago("p1", link["referencia"], 150000, estado="confirmado")
    _entregar(con, prov, "p1", aviso_id="av-2")
    assert db.obtener_factura(con, fid)["saldo"] == 0


def test_proveedor_caido_no_registra_nada():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    prov.caido = True

    _entregar(con, prov, "p1")
    assert db.obtener_factura(con, fid)["saldo"] == 150000
    assert cobros.MOTIVO_PROVEEDOR in _motivos(con)


def test_referencia_desconocida():
    con, prov = _entorno()
    _factura(con)
    prov.agregar_pago("p1", "fac-999-1", 150000)
    _entregar(con, prov, "p1")
    assert cobros.MOTIVO_REFERENCIA in _motivos(con)
    assert con.execute("SELECT COUNT(*) FROM pagos").fetchone()[0] == 0


# --- comisión --------------------------------------------------------------


def test_comision_no_toca_el_saldo():
    con, prov = _entorno()
    fid = _factura(con, monto=150000)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000, comision=4350)

    _entregar(con, prov, "p1")
    f = db.obtener_factura(con, fid)
    assert f["saldo"] == 0, "la factura se salda con lo que pagó el cliente"
    assert f["estado"] == "PAGADA"
    assert db.historial(con, fid)[0]["monto"] == 150000

    evento = con.execute("SELECT comision, neto FROM eventos_webhook").fetchone()
    assert evento["comision"] == 4350 and evento["neto"] == 145650


# --- reembolsos ------------------------------------------------------------


def test_reembolso_total():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    _entregar(con, prov, "p1", aviso_id="av-1")
    assert db.obtener_factura(con, fid)["saldo"] == 0

    prov.agregar_pago("p1", link["referencia"], 150000,
                      reembolsos=[{"id": "r1", "monto": 150000}])
    _entregar(con, prov, "p1", aviso_id="av-2")

    assert db.obtener_factura(con, fid)["saldo"] == 150000
    movs = db.historial(con, fid)
    assert [m["monto"] for m in movs] == [150000, -150000]
    assert all(m["revierte_a"] is None for m in movs), "un reembolso no es una reversión"


def test_pago_ya_reembolsado_registra_los_dos_movimientos():
    """`refunded` es un pago que ocurrió y se devolvió: el historial lo muestra."""
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000, estado="confirmado",
                      reembolsos=[{"id": "r1", "monto": 150000}])
    prov.pagos["p1"].estado_proveedor = "refunded"

    _entregar(con, prov, "p1")
    assert [m["monto"] for m in db.historial(con, fid)] == [150000, -150000]
    assert db.obtener_factura(con, fid)["saldo"] == 150000
    assert db.excepciones(con) == [], "no es una excepción: se procesó bien"


def test_estado_crudo_del_proveedor_en_la_bandeja():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000, estado="pendiente")
    prov.pagos["p1"].estado_proveedor = "in_process"
    _entregar(con, prov, "p1")
    assert "pago in_process" in _motivos(con), "la bandeja muestra el estado real"


def test_dos_reembolsos_parciales_conviven():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    _entregar(con, prov, "p1", aviso_id="av-1")

    prov.agregar_pago("p1", link["referencia"], 150000, reembolsos=[
        {"id": "r1", "monto": 50000}, {"id": "r2", "monto": 30000},
    ])
    _entregar(con, prov, "p1", aviso_id="av-2")

    assert db.obtener_factura(con, fid)["saldo"] == 80000
    assert sorted(m["monto"] for m in db.historial(con, fid)) == [-50000, -30000, 150000]


def test_reembolso_repetido_registra_uno_solo():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000,
                      reembolsos=[{"id": "r1", "monto": 50000}])
    _entregar(con, prov, "p1", aviso_id="av-1")
    _entregar(con, prov, "p1", aviso_id="av-2")

    assert [m["monto"] for m in db.historial(con, fid)] == [150000, -50000]
    assert db.obtener_factura(con, fid)["saldo"] == 50000


# --- links -----------------------------------------------------------------


def test_link_expirado_se_regenera_al_consultarlo():
    con, prov = _entorno()
    fid = _factura(con)
    prov.horas_vigencia = -1  # nace expirado
    primero = cobros.encender_cobro_por_link(con, prov, fid)
    assert db.link_por_referencia(con, PROVEEDOR, primero["referencia"])["estado"] == "expirado"

    prov.horas_vigencia = 24
    segundo = cobros.link_para_factura(con, prov, fid)
    assert segundo["id"] != primero["id"]
    assert segundo["estado"] == "vigente"
    historial = db.links_de_factura(con, fid)
    assert len(historial) == 2, "el link anterior se conserva en el historial"
    assert {l["estado"] for l in historial} == {"vigente", "expirado"}


def test_link_vigente_no_se_regenera():
    con, prov = _entorno()
    fid = _factura(con)
    primero = cobros.encender_cobro_por_link(con, prov, fid)
    segundo = cobros.link_para_factura(con, prov, fid)
    assert primero["id"] == segundo["id"]
    assert len(db.links_de_factura(con, fid)) == 1


def test_un_solo_link_vigente():
    con, prov = _entorno()
    fid = _factura(con)
    cobros.encender_cobro_por_link(con, prov, fid)
    cobros.generar_link(con, prov, fid)
    vigentes = [l for l in db.links_de_factura(con, fid) if l["estado"] == "vigente"]
    assert len(vigentes) == 1


def test_link_pagado_deja_de_estar_vigente():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    _entregar(con, prov, "p1")
    assert db.link_por_referencia(con, PROVEEDOR, link["referencia"])["estado"] == "pagado"
    assert db.link_vigente(con, fid) is None


def test_proveedor_caido_no_persiste_link_ni_cambia_el_modo():
    con, prov = _entorno()
    fid = _factura(con)
    prov.caido = True
    try:
        cobros.encender_cobro_por_link(con, prov, fid)
        raise AssertionError("debió propagarse el error del proveedor")
    except ErrorProveedor:
        pass
    assert db.links_de_factura(con, fid) == []
    assert db.obtener_factura(con, fid)["cobro_por_link"] == 0


# --- interruptor y bloqueo del pago manual ---------------------------------


def test_pago_manual_bloqueado_con_cobro_por_link():
    con, prov = _entorno()
    fid = _factura(con)
    cobros.encender_cobro_por_link(con, prov, fid)
    try:
        db.registrar_pago(con, fid, 50000)
        raise AssertionError("el pago manual debió rechazarse")
    except ValueError as e:
        assert "link" in str(e)
    assert db.historial(con, fid) == []


def test_apagar_el_interruptor_rehabilita_el_pago_manual():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)

    cobros.apagar_cobro_por_link(con, prov, fid)
    assert db.obtener_factura(con, fid)["cobro_por_link"] == 0
    assert link["preferencia_id"] in prov.canceladas
    assert db.link_por_referencia(con, PROVEEDOR, link["referencia"])["estado"] == "cancelado"
    assert db.link_vigente(con, fid) is None

    db.registrar_pago(con, fid, 50000)
    assert db.obtener_factura(con, fid)["saldo"] == 100000


def test_apagado_no_regenera_links():
    con, prov = _entorno()
    fid = _factura(con)
    cobros.encender_cobro_por_link(con, prov, fid)
    cobros.apagar_cobro_por_link(con, prov, fid)
    assert cobros.link_para_factura(con, prov, fid) is None
    assert len(db.links_de_factura(con, fid)) == 1


def test_movimiento_automatico_pasa_con_el_interruptor_encendido():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    _entregar(con, prov, "p1")
    assert db.obtener_factura(con, fid)["saldo"] == 0


# --- reconciliación --------------------------------------------------------


def test_reconciliacion_recupera_un_aviso_perdido():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)  # el aviso nunca llega

    resumen = cobros.reconciliar(con, prov)
    assert resumen["consultados"] == 1 and resumen["registrados"] == 1
    assert db.obtener_factura(con, fid)["saldo"] == 0
    mov = db.historial(con, fid)[0]
    assert mov["origen"] == PROVEEDOR and mov["reconciliacion_id"] == resumen["id"]


def test_reconciliacion_deja_excepcion_sin_factura():
    con, prov = _entorno()
    prov.agregar_pago("p9", "fac-404-1", 99000)
    resumen = cobros.reconciliar(con, prov)
    assert resumen["excepciones"] == 1 and resumen["registrados"] == 0
    assert cobros.MOTIVO_HUERFANO in _motivos(con)
    assert con.execute("SELECT COUNT(*) FROM pagos").fetchone()[0] == 0


def test_reconciliacion_con_proveedor_caido():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    prov.caido = True
    try:
        cobros.reconciliar(con, prov)
        raise AssertionError("debió informar que no pudo completarse")
    except ErrorProveedor:
        pass
    assert db.obtener_factura(con, fid)["saldo"] == 150000
    assert con.execute("SELECT COUNT(*) FROM pagos").fetchone()[0] == 0


def test_la_reconciliacion_saca_el_aviso_fallido_de_la_bandeja():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    prov.caido = True
    _entregar(con, prov, "p1")
    assert len(db.excepciones(con)) == 1

    prov.caido = False
    cobros.reconciliar(con, prov)
    assert db.excepciones(con) == [], "el aviso dejó de ser una excepción pendiente"
    assert db.obtener_factura(con, fid)["saldo"] == 0


def test_reconciliacion_recupera_aunque_la_busqueda_por_fecha_no_devuelva_nada():
    """El sandbox real no devuelve resultados por rango de fechas; la
    reconciliación igual recupera preguntando por cada link pendiente."""
    con, prov = _entorno()
    prov.busqueda_por_fecha_vacia = True
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)

    r = cobros.reconciliar(con, prov)
    assert r["registrados"] == 1
    assert db.obtener_factura(con, fid)["saldo"] == 0


def test_resumen_de_reconciliacion():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    prov.agregar_pago("p9", "fac-404-1", 5000)
    prov.agregar_pago("p8", "fac-404-2", 5000, estado="pendiente")  # no confirmado

    r = cobros.reconciliar(con, prov)
    assert (r["consultados"], r["registrados"], r["excepciones"]) == (2, 1, 1)
    assert r["desde"] < r["hasta"] or r["desde"] == r["hasta"]


# --- la bandeja no escribe -------------------------------------------------


def test_la_bandeja_es_solo_lectura():
    con, prov = _entorno()
    _factura(con)
    prov.agregar_pago("p1", "fac-999-1", 150000)
    _entregar(con, prov, "p1")

    antes = con.execute("SELECT COUNT(*) FROM pagos").fetchone()[0]
    excepciones = db.excepciones(con)
    assert len(excepciones) == 1
    assert excepciones[0]["cuerpo_crudo"], "la bandeja muestra el contenido recibido"
    assert excepciones[0]["recibido_en"] and excepciones[0]["aviso_id"]
    assert con.execute("SELECT COUNT(*) FROM pagos").fetchone()[0] == antes


def test_motivos_distinguibles_en_la_bandeja():
    con, prov = _entorno()
    fid = _factura(con)
    link = cobros.encender_cobro_por_link(con, prov, fid)
    prov.agregar_pago("p1", link["referencia"], 150000)
    _entregar(con, prov, "p1", aviso_id="a1", firma_ok=False)      # firma
    prov.agregar_pago("p2", "fac-404-1", 1000)
    _entregar(con, prov, "p2", aviso_id="a2")                       # referencia
    prov.caido = True
    prov.pagos["p3"] = prov.pagos["p1"]
    _entregar(con, prov, "p3", aviso_id="a3")                       # proveedor

    motivos = set(_motivos(con))
    assert motivos == {cobros.MOTIVO_FIRMA, cobros.MOTIVO_REFERENCIA, cobros.MOTIVO_PROVEEDOR}


if __name__ == "__main__":
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for prueba in pruebas:
        prueba()
        print(f"✓ {prueba.__name__}")
    print(f"\n{len(pruebas)} pruebas OK")
