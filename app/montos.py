"""Montos en la unidad mínima (centavos). Nunca punto flotante."""

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

# Decimales de la moneda. 0 para monedas que no los tienen (CLP).
# Determinado por la cuenta del proveedor; solo afecta parseo y despliegue.
DECIMALES = 0
_UNIDAD = Decimal(10) ** -DECIMALES
_FACTOR = Decimal(10) ** DECIMALES

# Sin decimales, un separador solo puede ser de miles: grupos de tres dígitos.
_SOLO_ENTERO = re.compile(r"^-?\d+$")
_CON_MILES = re.compile(r"^-?\d{1,3}(?:[.,]\d{3})+$")


def a_centavos(texto) -> int:
    """'1,234.56' | '1234,56' -> 123456 con 2 decimales; '1,234' -> 1234 con 0.

    Rechaza lo ambiguo en vez de adivinar: un monto mal leído es plata mal
    registrada.
    """
    if isinstance(texto, int):
        return texto
    s = str(texto).strip().replace(" ", "")
    if not s:
        raise ValueError("El monto es obligatorio")
    if DECIMALES == 0:
        # '1234.56' no se interpreta como 123456: se rechaza.
        if not (_SOLO_ENTERO.match(s) or _CON_MILES.match(s)):
            raise ValueError(f"Monto inválido: {texto!r} (la moneda no admite decimales)")
        s = s.replace(",", "").replace(".", "")
    elif "," in s and "." in s:
        s = s.replace(",", "")  # coma = miles, punto = decimal
    else:
        s = s.replace(",", ".")  # coma sola = separador decimal
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"Monto inválido: {texto!r}")
    if d != d.quantize(_UNIDAD):
        raise ValueError(f"El monto admite como máximo {DECIMALES} decimales")
    return int(d * _FACTOR)


def formatear(centavos: int) -> str:
    """123456 -> '1,234.56'"""
    signo = "-" if centavos < 0 else ""
    d = Decimal(abs(int(centavos))) / _FACTOR
    return f"{signo}{d:,.{DECIMALES}f}"
