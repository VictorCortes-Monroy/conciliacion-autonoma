"""Libreta de facturas: listado, detalle, bandeja y webhook. Ver design.md."""

import json
from pathlib import Path
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, Form, Header, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import cobros, db
from app.mercadopago import (
    ClienteMercadoPago, ErrorProveedor, cargar_config, firma_valida,
)
from app.montos import a_centavos, formatear

app = FastAPI(title="Libreta de facturas")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters["monto"] = formatear

_con = db.inicializar(db.conectar())
_config = cargar_config()  # falla al arrancar si faltan credenciales
_mp = ClienteMercadoPago(_config)


def _volver(destino: str, error: str | None = None, aviso: str | None = None):
    if error:
        destino += f"?error={quote(error)}"
    elif aviso:
        destino += f"?aviso={quote(aviso)}"
    return RedirectResponse(destino, status_code=303)


# --- vistas ----------------------------------------------------------------


@app.get("/")
def listado(request: Request, error: str | None = None, aviso: str | None = None):
    return templates.TemplateResponse(
        request,
        "listado.html",
        {
            "facturas": db.listar_facturas(_con),
            "ag": db.agregados(_con),
            "excepciones": len(db.excepciones(_con)),
            "error": error,
            "aviso": aviso,
        },
    )


@app.post("/facturas")
def crear(cliente: str = Form(""), monto: str = Form(""), fecha_venc: str = Form("")):
    try:
        db.crear_factura(_con, cliente, a_centavos(monto), fecha_venc)
    except ValueError as e:
        return _volver("/", str(e))
    return _volver("/")


@app.get("/facturas/{factura_id}")
def detalle(request: Request, factura_id: int, error: str | None = None,
            aviso: str | None = None):
    factura = db.obtener_factura(_con, factura_id)
    if factura is None:
        return _volver("/", "La factura no existe")
    link, problema = None, None
    if factura["cobro_por_link"]:
        try:
            link = cobros.link_para_factura(_con, _mp, factura_id)
        except ErrorProveedor as e:
            problema = f"No se pudo obtener el link: {e}"
    return templates.TemplateResponse(
        request,
        "detalle.html",
        {
            "f": factura,
            "movimientos": db.historial(_con, factura_id),
            "link": link,
            "links": db.links_de_factura(_con, factura_id),
            "error": error or problema,
            "aviso": aviso,
        },
    )


@app.post("/facturas/{factura_id}/pagos")
def pagar(factura_id: int, monto: str = Form(""), fecha: str = Form("")):
    try:
        db.registrar_pago(_con, factura_id, a_centavos(monto), fecha or None)
    except ValueError as e:
        return _volver(f"/facturas/{factura_id}", str(e))
    return _volver(f"/facturas/{factura_id}")


@app.post("/pagos/{pago_id}/revertir")
def revertir(pago_id: int):
    pago = _con.execute("SELECT factura_id FROM pagos WHERE id = ?", (pago_id,)).fetchone()
    if pago is None:
        return _volver("/", "El movimiento no existe")
    try:
        db.revertir_pago(_con, pago_id)
    except ValueError as e:
        return _volver(f"/facturas/{pago['factura_id']}", str(e))
    return _volver(f"/facturas/{pago['factura_id']}")


# --- cobro por link --------------------------------------------------------


@app.post("/facturas/{factura_id}/cobro-por-link")
def cambiar_modo_cobro(factura_id: int, activar: str = Form("")):
    try:
        if activar == "1":
            cobros.encender_cobro_por_link(_con, _mp, factura_id)
        else:
            cobros.apagar_cobro_por_link(_con, _mp, factura_id)
    except (ValueError, ErrorProveedor) as e:
        return _volver(f"/facturas/{factura_id}", str(e))
    return _volver(f"/facturas/{factura_id}")


# --- webhook ---------------------------------------------------------------


@app.post("/webhooks/mercadopago")
async def webhook(
    request: Request,
    tareas: BackgroundTasks,
    x_signature: str = Header(default=""),
    x_request_id: str = Header(default=""),
):
    """Persiste el aviso y responde. El trabajo real ocurre después.

    Consultar la API del proveedor dentro del request haría que un proveedor
    lento provocara timeouts y, con ellos, más reintentos.
    """
    crudo = await request.body()
    try:
        cuerpo = json.loads(crudo)
    except (json.JSONDecodeError, UnicodeDecodeError):
        cuerpo = {}
    # El proveedor manda el identificador en la query string además del cuerpo,
    # y firma sobre ese valor.
    q = request.query_params
    data_id = str(
        q.get("data.id") or q.get("id")
        or (cuerpo.get("data") or {}).get("id", "")
        or cuerpo.get("resource", "")
    )
    aviso_id = str(cuerpo.get("id") or x_request_id or data_id or "sin-id")

    ok = firma_valida(_config.webhook_secret, x_signature, x_request_id, data_id)
    evento_id = cobros.recibir_aviso(_con, crudo, aviso_id, ok,
                                     firma=x_signature, request_id=x_request_id)
    if evento_id is not None:
        tareas.add_task(cobros.procesar_evento, _con, _mp, evento_id)
    return {"recibido": True}


# --- conciliación ----------------------------------------------------------


@app.get("/excepciones")
def bandeja(request: Request, error: str | None = None, aviso: str | None = None):
    """Solo lectura: muestra qué no se pudo procesar y por qué."""
    return templates.TemplateResponse(
        request,
        "excepciones.html",
        {
            "excepciones": db.excepciones(_con),
            "reconciliaciones": _con.execute(
                "SELECT * FROM reconciliaciones ORDER BY id DESC LIMIT 5"
            ).fetchall(),
            "error": error,
            "aviso": aviso,
        },
    )


@app.post("/reconciliar")
def ejecutar_reconciliacion(dias: str = Form("7")):
    try:
        r = cobros.reconciliar(_con, _mp, dias=int(dias or 7))
    except ErrorProveedor as e:
        return _volver("/excepciones", f"La reconciliación no pudo completarse: {e}")
    return _volver(
        "/excepciones",
        aviso=(f"Reconciliación {r['desde']} a {r['hasta']}: {r['consultados']} "
               f"consultados, {r['registrados']} registrados, "
               f"{r['excepciones']} excepciones"),
    )
