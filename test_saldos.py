"""Verificación del núcleo: saldos, estados, vencimiento y montos.

Corre con:  python test_saldos.py
"""

from datetime import date, timedelta

from app import db
from app.montos import DECIMALES, a_centavos, formatear


def _con():
    return db.inicializar(db.conectar(":memory:"))


def _dias(n):
    return (date.today() + timedelta(days=n)).isoformat()


def test_saldo_sin_pagos():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 150000, _dias(10))
    f = db.obtener_factura(con, fid)
    assert f["saldo"] == 150000
    assert f["estado"] == "PENDIENTE"
    assert f["vencida"] == 0


def test_pago_parcial():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 150000, _dias(10))
    db.registrar_pago(con, fid, 60000)
    f = db.obtener_factura(con, fid)
    assert f["saldo"] == 90000
    assert f["estado"] == "PARCIAL"


def test_parciales_suman_exacto():
    con = _con()
    total = a_centavos("1000")
    fid = db.crear_factura(con, "Cliente A", total, _dias(10))
    tercio = total // 3  # no divide exacto: el último abono tiene que cerrar el saldo
    for parte in (tercio, tercio, total - 2 * tercio):
        db.registrar_pago(con, fid, parte)
    f = db.obtener_factura(con, fid)
    assert f["saldo"] == 0, f["saldo"]
    assert f["estado"] == "PAGADA"


def test_sobrepago():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 100000, _dias(10))
    db.registrar_pago(con, fid, 120000)
    f = db.obtener_factura(con, fid)
    assert f["saldo"] == -20000
    assert f["estado"] == "PAGADA"


def test_reversion_devuelve_el_saldo():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 150000, _dias(10))
    previo = db.obtener_factura(con, fid)["saldo"]
    pid = db.registrar_pago(con, fid, 50000)
    assert db.obtener_factura(con, fid)["saldo"] == previo - 50000

    db.revertir_pago(con, pid)
    assert db.obtener_factura(con, fid)["saldo"] == previo

    movs = db.historial(con, fid)
    assert len(movs) == 2, "el movimiento original debe seguir en el historial"
    assert movs[0]["monto"] == 50000 and movs[0]["revierte_a"] is None
    assert movs[1]["monto"] == -50000 and movs[1]["revierte_a"] == pid
    assert movs[0]["revertido"] == 1


def test_reversion_no_se_revierte():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 150000, _dias(10))
    pid = db.registrar_pago(con, fid, 50000)
    rid = db.revertir_pago(con, pid)
    for pago_id, motivo in ((pid, "ya revertido"), (rid, "es una reversión")):
        try:
            db.revertir_pago(con, pago_id)
            raise AssertionError(f"debió rechazarse: {motivo}")
        except ValueError:
            pass


def test_vencida_solo_con_saldo():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 100000, _dias(-5))
    f = db.obtener_factura(con, fid)
    assert f["vencida"] == 1
    assert f["dias_atraso"] == 5

    db.registrar_pago(con, fid, 100000)
    f = db.obtener_factura(con, fid)
    assert f["estado"] == "PAGADA"
    assert f["vencida"] == 0, "una factura pagada deja de estar vencida"


def test_parcial_y_vencida_a_la_vez():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 100000, _dias(-3))
    db.registrar_pago(con, fid, 40000)
    f = db.obtener_factura(con, fid)
    assert f["estado"] == "PARCIAL" and f["vencida"] == 1


def test_factura_vigente_no_vence():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 100000, _dias(0))
    assert db.obtener_factura(con, fid)["vencida"] == 0


def test_validaciones_factura():
    con = _con()
    for args, motivo in (
        (("", 1000, _dias(5)), "cliente vacío"),
        (("A", 0, _dias(5)), "monto cero"),
        (("A", -100, _dias(5)), "monto negativo"),
        (("A", 1000, ""), "sin fecha"),
    ):
        try:
            db.crear_factura(con, *args)
            raise AssertionError(f"debió rechazarse: {motivo}")
        except ValueError:
            pass
    assert db.listar_facturas(con) == [], "ninguna factura inválida se persistió"


def test_validaciones_pago():
    con = _con()
    fid = db.crear_factura(con, "Cliente A", 100000, _dias(5))
    for args, motivo in (((fid, 0), "monto cero"), ((999, 1000), "factura inexistente")):
        try:
            db.registrar_pago(con, *args)
            raise AssertionError(f"debió rechazarse: {motivo}")
        except ValueError:
            pass
    assert db.historial(con, fid) == []


def test_agregados():
    con = _con()
    db.crear_factura(con, "Cliente A", 100000, _dias(-2))   # vencida
    db.crear_factura(con, "Cliente A", 50000, _dias(10))    # vigente
    fid = db.crear_factura(con, "Cliente B", 80000, _dias(10))
    db.registrar_pago(con, fid, 80000)                      # pagada, no suma

    ag = db.agregados(con)
    assert ag["adeudado"] == 150000
    assert ag["vencido"] == 100000
    assert [(r["cliente"], r["deuda"]) for r in ag["por_cliente"]] == [("Cliente A", 150000)]


def test_montos_ida_y_vuelta():
    """El helper es el único lugar donde cambia la escala, en ambos sentidos."""
    for unidades in (0, 1, 99, 100, 123456, 1234567890, -20000):
        assert a_centavos(formatear(unidades)) == unidades, unidades

    invalidos = ["", "abc"]
    if DECIMALES == 0:
        assert a_centavos("1,234") == 1234
        assert a_centavos("1.234") == 1234
        assert a_centavos("1234") == 1234
        assert formatear(1234) == "1,234"
        assert formatear(-20000) == "-20,000"
        # con moneda sin decimales, '1234.56' se rechaza en vez de leerse 123456
        invalidos += ["1234.56", "10.5", "1.23"]
    else:
        assert a_centavos("1234,56") == 123456
        assert a_centavos("1,234.56") == 123456
        assert a_centavos("1234.5") == 123450
        assert formatear(123456) == "1,234.56"
        assert formatear(-20000) == "-200.00"
        invalidos += ["10.005"]

    for invalido in invalidos:
        try:
            a_centavos(invalido)
            raise AssertionError(f"debió rechazarse: {invalido!r}")
        except ValueError:
            pass


if __name__ == "__main__":
    pruebas = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for prueba in pruebas:
        prueba()
        print(f"✓ {prueba.__name__}")
    print(f"\n{len(pruebas)} pruebas OK")
