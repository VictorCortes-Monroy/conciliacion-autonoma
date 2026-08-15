## Why

Hoy la conciliación de cobros se hace a mano: no hay un lugar único que responda quién debe, cuánto y desde cuándo. El objetivo de esta primera etapa no es detectar pagos automáticamente, sino tener el registro confiable sobre el cual esa automatización pueda apoyarse después.

El pago ocurre fuera del sistema (transferencia por cualquier medio) y se registra manualmente. La utilidad no está en detectar el pago, sino en que todo lo demás —saldo, vencimiento, deuda por cliente— se derive solo.

## What Changes

- Nueva app interna (sin autenticación) con dos vistas: listado de facturas y detalle de una factura.
- Crear facturas con cliente, monto y fecha de vencimiento.
- Registrar pagos contra una factura, con monto propio: soporta **pagos parciales**.
- Revertir un pago registrando un movimiento de monto negativo (registro append-only; no se borra ni se edita historial).
- Estado de cobro **derivado**, nunca almacenado: `PENDIENTE` / `PARCIAL` / `PAGADA` según `saldo = monto − Σ pagos`.
- Vencimiento **derivado**, nunca almacenado: una factura está vencida si `fecha_venc < hoy` y su saldo es mayor a cero. Es un eje independiente del estado de cobro (una factura puede estar `PARCIAL` y vencida a la vez).
- Vista agregada de deuda: total adeudado, qué vence próximamente, qué está vencido y hace cuántos días.
- Todo registro de pago entra por un único punto (`registrar_pago`), con el origen marcado como `manual`, para que futuras fuentes (importación de cartola, webhook) se enchufen sin cambiar el modelo.

Fuera de alcance en esta etapa:

- Detección automática de pagos (importación de cartola, webhooks, matching).
- Link de pago (simulado o real): el cobro ocurre por fuera.
- Un pago que cubre varias facturas (relación N:M).
- Anular una factura ya pagada.
- Autenticación, usuarios y multi-tenancy.

El sobrepago no se bloquea: se permite y queda visible como saldo negativo. Validar `Σ pagos ≤ monto` exigiría lock o trigger, y es un dato anómalo a observar, no un caso a resolver.

## Capabilities

### New Capabilities

- `facturas`: creación y consulta de facturas; cálculo de saldo; estado de cobro y condición de vencimiento derivados, nunca almacenados; vistas agregadas de deuda.
- `pagos`: registro append-only de pagos contra una factura, incluyendo pagos parciales y reversiones por monto negativo; punto único de entrada con origen identificado.

### Modified Capabilities

Ninguna: el repositorio no tiene specs previas.

## Impact

- **Repositorio**: proyecto nuevo, sin código existente. Esta change introduce el stack inicial.
- **Stack**: FastAPI + plantillas Jinja + SQLite. Sin build de frontend, un proceso, base de datos en un archivo.
- **Restricciones no negociables por tratarse de datos financieros**:
  - Los montos se guardan como `INTEGER` en la unidad mínima (centavos). Nunca punto flotante.
  - El historial de pagos es append-only: sin `UPDATE` ni `DELETE` sobre movimientos ya registrados.
  - Las invariantes viven en la base de datos como constraints (`monto > 0` en facturas, `monto <> 0` en pagos, claves foráneas, `NOT NULL`), no solo en código de aplicación.
- **Migración de datos**: ninguna; no hay sistema previo.
