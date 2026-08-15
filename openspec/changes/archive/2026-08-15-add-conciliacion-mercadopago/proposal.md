## Why

La fase 1 dejó el registro confiable, pero el pago se sigue detectando a mano: alguien mira la cartola y marca. Esta fase cierra ese lazo — el cliente paga por un link y el estado de la factura cambia solo.

La costura ya está puesta: `registrar_pago(..., origen)` es un punto único de escritura pensado para recibir fuentes automáticas. Lo que falta no es el modelo de saldos, es todo lo que ocurre antes de esa llamada: generar el link, recibir el aviso, verificar que es cierto, evitar duplicados y detectar lo que se perdió.

## What Changes

**Cobro por link**

- Generar en MercadoPago una preferencia de pago por factura, con la factura identificada en `external_reference`, y exponer su URL.
- Los links son 1:N con la factura: si el vigente expira, se genera otro **a demanda** (al consultar el link), no por un proceso periódico.
- Nuevo interruptor por factura `cobro_por_link`. Con el interruptor activo se mantiene un link vigente y **se desactiva la marca manual de pago**; al apagarlo se cancela el link vigente y la marca manual vuelve a estar disponible.
- **BREAKING** (de comportamiento): una factura con `cobro_por_link` activo ya no admite registrar pagos desde la interfaz.

**Recepción de avisos**

- Endpoint público de webhook para MercadoPago. La notificación de MP contiene solo el identificador del pago, no sus datos: el monto y el estado se obtienen consultando la API de MP.
- La recepción se separa del procesamiento: el aviso se persiste crudo y se responde de inmediato; el procesamiento ocurre aparte y puede fallar sin afectar la respuesta.
- Verificación de la firma del aviso; los avisos con firma inválida se persisten y se descartan sin procesar.
- Un pago se traduce a movimiento solo cuando MercadoPago lo reporta como confirmado/capturado. Los estados intermedios se registran como eventos pero no mueven el saldo.
- Los reembolsos y contracargos informados por MercadoPago se registran como movimientos negativos independientes, con su propia referencia externa.

**Idempotencia en dos niveles**

- `eventos_webhook` es único por (proveedor, identificador de aviso): reintentos del mismo aviso no se reprocesan.
- Los movimientos de pago llevan una **referencia externa única** por proveedor: aunque MercadoPago emita varios avisos sobre el mismo pago, el dinero se registra una sola vez.

**Conciliación**

- Bandeja de excepciones **de solo lectura**: muestra los avisos recibidos que no se convirtieron en movimiento y el motivo. No ejecuta acciones ni escribe movimientos.
- Reconciliación de respaldo contra la API de MercadoPago: consulta los pagos confirmados de un período y registra los que falten. Es el único mecanismo automático de reparación cuando un webhook se pierde.

**Montos**

- El movimiento se registra por el monto que pagó el cliente (bruto). La comisión del proveedor se guarda como dato del evento y no afecta el saldo de la factura.
- La cantidad de decimales de la moneda queda determinada por la cuenta de MercadoPago utilizada (pregunta que la fase 1 dejó abierta).

Fuera de alcance:

- Cobro presencial, suscripciones, marketplace y cualquier producto de MercadoPago distinto de checkout con preferencia.
- Reprocesar un aviso fallido desde la interfaz: la bandeja solo muestra.
- Conciliar la cuenta bancaria contra las liquidaciones de MercadoPago (el neto recibido); esta fase concilia facturas contra pagos, no cuentas contra depósitos.
- Autenticación de usuarios: la app interna sigue sin login. El endpoint de webhook es público por necesidad y se protege por firma.
- Múltiples proveedores simultáneos. El modelo guarda el proveedor en cada registro, pero solo se implementa MercadoPago.

## Capabilities

### New Capabilities

- `links-pago`: generación y ciclo de vida de los links de cobro de una factura — creación de la preferencia en el proveedor, vigencia, expiración, regeneración a demanda, cancelación y el interruptor `cobro_por_link` de la factura.
- `webhooks-pago`: recepción de avisos del proveedor — verificación de firma, persistencia cruda, respuesta inmediata, procesamiento diferido, consulta del pago en la API del proveedor y traducción a movimientos con idempotencia por pago.
- `conciliacion`: bandeja de excepciones de solo lectura y reconciliación de respaldo contra la API del proveedor para recuperar pagos cuyo aviso no llegó o no pudo procesarse.

### Modified Capabilities

- `pagos`: el origen deja de ser exclusivamente `manual`; los movimientos incorporan una referencia externa única por proveedor; los reembolsos del proveedor se modelan como movimientos negativos independientes en lugar de reversiones (`revierte_a` queda reservado para deshacer un movimiento desde la interfaz).
- `facturas`: se incorpora el interruptor `cobro_por_link`, que condiciona la disponibilidad del registro manual de pagos.

## Impact

- **Código afectado**: `app/db.py` (nuevas tablas, columna de referencia externa en `pagos`, interruptor en `facturas`), `app/main.py` (endpoint de webhook, rutas de link y de conciliación), plantillas de listado y detalle, más módulos nuevos para el cliente de MercadoPago y el procesamiento de eventos.
- **Dependencias nuevas**: cliente HTTP para llamar a la API de MercadoPago (SDK oficial o HTTP directo).
- **Configuración**: credenciales de prueba de MercadoPago y secreto de firma en variables de entorno, nunca en el repositorio. Solo credenciales de sandbox.
- **Infraestructura**: MercadoPago no alcanza `localhost`; el sandbox exige una URL pública accesible por HTTPS (túnel o despliegue) para recibir avisos.
- **Migración de datos**: el esquema existente se amplía con tablas y columnas nuevas; las facturas y movimientos ya registrados siguen siendo válidos, con `cobro_por_link` apagado y sin referencia externa.
- **Restricciones heredadas que se mantienen**: montos como enteros de la unidad mínima, historial de movimientos append-only, invariantes expresadas como constraints de base de datos.
- **Herramienta de desarrollo**: se contempla usar el plugin oficial de MercadoPago para Claude Code durante la implementación. Es tooling de desarrollo y no forma parte del sistema desplegado.
