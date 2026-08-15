"""Único punto de contacto con MercadoPago.

El resto del sistema no ve campos, nombres ni estados de MercadoPago: solo los
tipos normalizados de este módulo. Los tests usan un doble con la misma
interfaz y no tocan la red.

Verificado contra la documentación del proveedor: el manifiesto de firma
(incluida la regla de omitir los componentes ausentes), los estados de un pago
y los parámetros de búsqueda por período. Sin verificar contra la API real: los
nombres de campos de la respuesta de consulta de un pago. Si alguno cambia, el
cambio queda contenido en este archivo.
"""

import hashlib
import hmac
import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import certifi

from app.montos import a_centavos

PROVEEDOR = "mercadopago"
BASE_URL = "https://api.mercadopago.com"

# Bundle de CAs propio: el Python de python.org en macOS no usa el del sistema.
# Contra una API de pagos la verificación de certificado no se desactiva.
_CONTEXTO_TLS = ssl.create_default_context(cafile=certifi.where())

# Estados de MercadoPago que significan "el pago ocurrió".
# `refunded` entra porque el cobro sí sucedió y después se devolvió: registrar
# ambos movimientos deja el historial fiel y el saldo neto correcto. Un
# `charged_back` NO entra: el contracargo no viene en `refunds`, así que
# registrar solo el positivo dejaría plata que no está.
ESTADOS_CONFIRMADOS = {"approved", "refunded"}


class ErrorProveedor(Exception):
    """El proveedor no respondió o respondió algo inservible."""


@dataclass
class PagoProveedor:
    """Un pago tal como lo entiende este sistema, ya normalizado."""

    id: str
    estado: str  # 'confirmado' | 'pendiente' | 'rechazado' | 'cancelado'
    monto: int  # unidad mínima, siempre
    referencia: str | None
    comision: int = 0
    neto: int = 0
    reembolsos: list = field(default_factory=list)  # [{'id': str, 'monto': int}]
    estado_proveedor: str = ""  # el nombre crudo, para que la bandeja no mienta

    def __post_init__(self):
        if not self.estado_proveedor:
            self.estado_proveedor = self.estado

    @property
    def confirmado(self) -> bool:
        return self.estado == "confirmado"


@dataclass
class LinkProveedor:
    preferencia_id: str
    url: str
    expira_en: str


# --- configuración ---------------------------------------------------------


def _cargar_dotenv(ruta: Path) -> None:
    if not ruta.exists():
        return
    for linea in ruta.read_text().splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip("'\""))


@dataclass
class Config:
    access_token: str
    webhook_secret: str
    url_publica: str
    horas_vigencia: int = 24
    base_url: str = BASE_URL
    sandbox: bool = False


def cargar_config(raiz: Path | None = None) -> Config:
    """Falla al arrancar, con nombres concretos, si falta algo."""
    raiz = raiz or Path(__file__).resolve().parent.parent
    _cargar_dotenv(raiz / ".env")
    faltan = [
        v for v in ("MP_ACCESS_TOKEN", "MP_WEBHOOK_SECRET", "MP_URL_PUBLICA")
        if not os.environ.get(v)
    ]
    if faltan:
        raise RuntimeError(
            "Faltan variables de entorno: " + ", ".join(faltan) +
            ". Copiá .env.example a .env y completá las credenciales de prueba."
        )
    return Config(
        access_token=os.environ["MP_ACCESS_TOKEN"],
        webhook_secret=os.environ["MP_WEBHOOK_SECRET"],
        url_publica=os.environ["MP_URL_PUBLICA"].rstrip("/"),
        horas_vigencia=int(os.environ.get("MP_HORAS_VIGENCIA", "24")),
        # Redirigible para pruebas locales sin salir a la red.
        base_url=os.environ.get("MP_BASE_URL", BASE_URL).rstrip("/"),
        sandbox=os.environ.get("MP_SANDBOX", "").strip() in ("1", "true", "True"),
    )


# --- verificación de firma -------------------------------------------------


def firma_valida(secreto: str, cabecera_firma: str, request_id: str, data_id: str) -> bool:
    """Verifica el HMAC del aviso. Comparación en tiempo constante.

    El manifiesto se arma con los valores de las cabeceras, no con el cuerpo
    parseado: reserializar el JSON cambiaría espacios y orden de claves.
    """
    if not cabecera_firma:
        return False
    partes = dict(
        p.split("=", 1) for p in cabecera_firma.split(",") if "=" in p
    )
    ts, recibido = partes.get("ts", "").strip(), partes.get("v1", "").strip()
    if not ts or not recibido:
        return False
    # El manifiesto omite los componentes ausentes en lugar de dejarlos vacíos,
    # y el id alfanumérico va en minúsculas.
    componentes = []
    if data_id:
        componentes.append(f"id:{data_id.lower()};")
    if request_id:
        componentes.append(f"request-id:{request_id};")
    componentes.append(f"ts:{ts};")
    manifiesto = "".join(componentes)
    esperado = hmac.new(
        secreto.encode(), manifiesto.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperado, recibido)


# --- cliente ---------------------------------------------------------------


class ClienteMercadoPago:
    """Las cuatro operaciones que el sistema necesita del proveedor."""

    def __init__(self, config: Config, base_url: str = None, timeout: int = 10):
        self.config = config
        self.base_url = base_url or config.base_url
        self.timeout = timeout

    def _pedir(self, metodo: str, ruta: str, cuerpo=None):
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(
            f"{self.base_url}{ruta}",
            data=datos,
            method=metodo,
            headers={
                "Authorization": f"Bearer {self.config.access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=_CONTEXTO_TLS) as r:
                return json.loads(r.read() or b"{}")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as e:
            raise ErrorProveedor(f"{metodo} {ruta}: {e}") from e

    def crear_preferencia(self, referencia: str, monto: int, descripcion: str) -> LinkProveedor:
        expira = datetime.now(timezone.utc) + timedelta(hours=self.config.horas_vigencia)
        cuerpo = {
            "items": [{
                "title": descripcion,
                "quantity": 1,
                "unit_price": _a_unidades(monto),
            }],
            "external_reference": referencia,
            "notification_url": f"{self.config.url_publica}/webhooks/mercadopago",
            "back_urls": {
                "success": f"{self.config.url_publica}/",
                "pending": f"{self.config.url_publica}/",
                "failure": f"{self.config.url_publica}/",
            },
            "expires": True,
            "expiration_date_to": expira.isoformat(),
        }
        r = self._pedir("POST", "/checkout/preferences", cuerpo)
        # Con credenciales de prueba el pago va por el checkout de sandbox;
        # `init_point` apunta al de producción y falla al confirmar.
        url = ((r.get("sandbox_init_point") or r.get("init_point")) if self.config.sandbox
               else (r.get("init_point") or r.get("sandbox_init_point")))
        if not r.get("id") or not url:
            raise ErrorProveedor("la preferencia creada no trae id ni url")
        return LinkProveedor(
            preferencia_id=str(r["id"]),
            url=url,
            expira_en=expira.astimezone().replace(tzinfo=None).isoformat(timespec="seconds"),
        )

    def cancelar_preferencia(self, preferencia_id: str) -> None:
        self._pedir("PUT", f"/checkout/preferences/{preferencia_id}", {"expires": True,
                    "expiration_date_to": datetime.now(timezone.utc).isoformat()})

    def consultar_pago(self, pago_id: str) -> PagoProveedor:
        return _normalizar(self._pedir("GET", f"/v1/payments/{pago_id}"))

    def buscar_pagos_confirmados(self, desde: str, hasta: str) -> list:
        ruta = (
            "/v1/payments/search?sort=date_approved&criteria=desc"
            f"&range=date_approved&begin_date={desde}T00:00:00.000-00:00"
            f"&end_date={hasta}T23:59:59.999-00:00&limit=100"
        )
        r = self._pedir("GET", ruta)
        # El filtro no va en la URL: "confirmado" lo define este módulo en un
        # solo lugar, e incluye los reembolsados.
        pagos = [_normalizar(p) for p in r.get("results", [])]
        return [p for p in pagos if p.confirmado]

    def buscar_pagos_por_referencia(self, referencia: str) -> list:
        """Pregunta por una referencia concreta.

        La búsqueda por rango de fechas no devuelve resultados con credenciales
        de usuario de prueba, así que este es el camino fiable: en vez de pedir
        "todos los pagos del período", se pregunta por cada link pendiente.
        """
        r = self._pedir("GET", f"/v1/payments/search?external_reference={referencia}")
        pagos = [_normalizar(p) for p in r.get("results", [])]
        return [p for p in pagos if p.confirmado]


def _a_unidades(centavos: int) -> float:
    from app.montos import DECIMALES
    return centavos / (10 ** DECIMALES) if DECIMALES else centavos


def _normalizar(p: dict) -> PagoProveedor:
    """Traduce la respuesta de MercadoPago a los tipos de este sistema."""
    try:
        estado_mp = p["status"]
        comisiones = sum(
            a_centavos(str(f.get("amount", 0))) for f in p.get("fee_details") or []
        )
        detalles = p.get("transaction_details") or {}
        return PagoProveedor(
            id=str(p["id"]),
            estado=(
                "confirmado" if estado_mp in ESTADOS_CONFIRMADOS
                else "cancelado" if estado_mp == "cancelled"
                else "rechazado" if estado_mp == "rejected"
                else "pendiente"
            ),
            monto=a_centavos(str(p["transaction_amount"])),
            referencia=p.get("external_reference"),
            comision=comisiones,
            neto=a_centavos(str(detalles.get("net_received_amount", 0) or 0)),
            estado_proveedor=estado_mp,
            reembolsos=[
                {"id": str(r["id"]), "monto": a_centavos(str(r["amount"]))}
                for r in p.get("refunds") or []
            ],
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ErrorProveedor(f"respuesta del proveedor ilegible: {e}") from e
