"""Reconciliación por línea de comandos.

    python -m app.reconciliar [--dias 7] [--desde AAAA-MM-DD --hasta AAAA-MM-DD]

Programarla es una línea de cron sobre este mismo comando.
"""

import argparse
import sys

from app import cobros, db
from app.mercadopago import ClienteMercadoPago, ErrorProveedor, cargar_config


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Recupera pagos confirmados sin movimiento")
    p.add_argument("--dias", type=int, default=7, help="ventana hacia atrás (por omisión 7)")
    p.add_argument("--desde", help="AAAA-MM-DD")
    p.add_argument("--hasta", help="AAAA-MM-DD")
    args = p.parse_args(argv)

    con = db.inicializar(db.conectar())
    cliente = ClienteMercadoPago(cargar_config())
    try:
        r = cobros.reconciliar(con, cliente, desde=args.desde, hasta=args.hasta,
                               dias=args.dias)
    except ErrorProveedor as e:
        print(f"La reconciliación no pudo completarse: {e}", file=sys.stderr)
        return 1
    print(f"Período {r['desde']} a {r['hasta']}")
    print(f"  consultados: {r['consultados']}")
    print(f"  registrados: {r['registrados']}")
    print(f"  excepciones: {r['excepciones']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
