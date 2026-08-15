## Context

Ver `proposal.md` para la motivación y `specs/` para los requisitos. La fase 1 dejó construido lo que esta fase reutiliza sin tocar: `registrar_pago(...)` como único punto de escritura sobre los movimientos, historial append-only, y saldo, estado y vencimiento derivados en la consulta.

Lo que cambia el problema respecto de la fase 1:

- Aparece un tercero que puede fallar, repetirse, llegar tarde y llegar fuera de orden.
- Aparece un endpoint **público** que provoca escrituras de dinero.
- Aparecen escritores concurrentes: el receptor del aviso, el procesador diferido y la reconciliación.
- El aviso de MercadoPago **no trae los datos del pago**: trae un identificador. El monto y el estado se obtienen consultando la API del proveedor.

## Goals / Non-Goals

**Goals:**

- Que un pago produzca exactamente un movimiento, sin importar cuántos avisos lleguen ni por qué camino se entere el sistema.
- Que ningún aviso se pierda sin dejar rastro, aunque no se pueda procesar.
- Que un webhook perdido sea recuperable sin intervención manual.
- Que el modelo de saldos de la fase 1 no requiera cambios estructurales.

**Non-Goals:**

- Cola de mensajes, workers o scheduler. El procesamiento diferido es dentro del mismo proceso.
- Alta disponibilidad del endpoint de webhook. Si el servicio está caído, MercadoPago reintenta y, en última instancia, la reconciliación repara.
- Soporte simultáneo de varios proveedores. El proveedor se guarda en cada registro, pero solo se implementa uno.

## Decisions

### Recibir y procesar son dos pasos, no uno

```
   POST /webhooks/mercadopago
        │
        ├─ leer body crudo, verificar firma
        ├─ INSERT en eventos_webhook  (ON CONFLICT DO NOTHING)
        ├─ responder 200                        ◄── acá termina la obligación
        │
        └─ procesar en segundo plano:
             GET /v1/payments/{id}  →  ¿confirmado?  →  registrar_pago
                                                     └→  motivo_no_proc
```

Procesar dentro del request obliga a esperar una llamada de red saliente antes de responder. Si MercadoPago está lento, el request expira, MercadoPago lo interpreta como fallo y reintenta — y se realimenta justo cuando el sistema ya está en problemas.

El paso diferido se implementa con `BackgroundTasks` de FastAPI: se ejecuta después de enviar la respuesta, en el mismo proceso, sin infraestructura adicional.

*Alternativa:* una cola real (Celery, RQ, Redis) da reintentos y durabilidad, pero agrega un servicio y un worker a un ejercicio de un archivo SQLite. Se descarta por una razón concreta, no por pereza: **la reconciliación de respaldo ya cubre el caso que la cola protegería**. Si el proceso muere entre la respuesta y el registro, el pago queda en MercadoPago y la próxima reconciliación lo levanta. La red de seguridad ya está en el alcance, así que la durabilidad del paso intermedio deja de ser crítica.

### Dos constraints de unicidad, con propósitos distintos

```
  UNIQUE (proveedor, aviso_id)        en eventos_webhook
      evita reprocesar el mismo aviso

  UNIQUE (proveedor, referencia_externa)  en pagos
      evita registrar dos veces la misma plata   ◄── esta cuida el dinero
```

La segunda es la importante. MercadoPago emite varios avisos sobre un mismo pago (creación, actualizaciones), así que deduplicar por aviso no alcanza: tres avisos distintos pueden hablar del mismo pago. Y es también lo que permite que el webhook y la reconciliación corran sin coordinarse — el que llegue segundo choca contra la constraint y no hace nada.

*Alternativa:* verificar con un `SELECT` previo antes de insertar. Se descarta: entre el `SELECT` y el `INSERT` cabe otro escritor. La unicidad la decide la base.

### Estado del link: derivar todo lo derivable

Siguiendo la disciplina de la fase 1, de los cuatro estados del link solo uno se almacena:

```
   cancelado  →  columna cancelado_en (es una decisión humana, no se deduce)
   pagado     →  existe un movimiento asociado a ese link
   expirado   →  expira_en < ahora
   vigente    →  ninguna de las anteriores
```

Una columna `estado` en `links_pago` habría que mantener sincronizada desde tres lugares distintos, que es exactamente el error que la fase 1 evitó no guardando el estado de la factura.

### `cobro_por_link` vive en la factura, no en el link

Si el interruptor fuera "existe un link vigente", la regeneración automática al expirar volvería permanente el bloqueo del registro manual: siempre habría un link vigente, y un cliente que transfiere por fuera dejaría la factura trabada sin salida.

```
   facturas.cobro_por_link = 1        facturas.cobro_por_link = 0
   ──────────────────────────         ──────────────────────────
   se mantiene un link vigente        no se generan links
   pago manual bloqueado              pago manual habilitado
   (si expira, se regenera            el link vigente queda cancelado
    al consultarlo)
```

Apagar el interruptor es la puerta de salida y cancela el link vigente en el proveedor. Los links quedan 1:N con la factura y el historial de intentos se conserva.

### Regeneración perezosa

Un link expirado se regenera **cuando alguien lo pide**, no por un proceso periódico. Un job que regenera links de facturas que nadie va a mirar produce trabajo y objetos en el proveedor sin que exista un destinatario. El costo de la pereza es que el primer acceso tras la expiración es más lento por una llamada a la API.

### Monto bruto, comisión aparte

```
   transaction_amount  →  el movimiento             (la factura se salda con esto)
   fee_details         →  dato del evento            (no afecta el saldo)
   net_received_amount →  dato del evento            (no afecta el saldo)
```

Registrar el neto dejaría toda factura cobrada por link con un saldo residual igual a la comisión, y esa deuda fantasma reintroduce la conciliación manual que la fase 1 vino a eliminar. La comisión es un gasto propio, no un menor pago del cliente.

MercadoPago expresa los montos en unidades de la moneda, no en la unidad mínima; la conversión a enteros pasa por el helper de `montos.py`, que ya es el único lugar donde ese cambio de escala ocurre.

### Reembolsos: movimientos propios, no reversiones

El índice único sobre `revierte_a` de la fase 1 permite una sola reversión por movimiento. MercadoPago admite **varios reembolsos parciales sobre un mismo pago**, de modo que modelarlos como reversión choca contra esa constraint al segundo reembolso.

```
   pago 150.000
     ├─ refund 50.000  → movimiento -50.000, referencia externa propia
     └─ refund 30.000  → movimiento -30.000, referencia externa propia
```

Cada reembolso es un hecho independiente con su propia identidad en el proveedor, no la anulación de otro hecho. `revierte_a` queda para lo que se creó: deshacer desde la interfaz un movimiento que registró una persona.

### Firma sobre el cuerpo exacto recibido

La verificación se hace sobre los bytes crudos del request, antes de cualquier parseo. Si el framework deserializa y vuelve a serializar el JSON, el orden de claves y los espacios cambian y la firma deja de calzar por motivos que no tienen nada que ver con la autenticidad. La comparación de firmas usa comparación en tiempo constante.

Como el aviso solo trae un identificador y los datos del pago se consultan con el token propio, una firma falsificada no puede inyectar montos: lo peor que logra es provocar una consulta inútil a la API. La firma filtra ruido; el token es la defensa real.

### Concurrencia en SQLite

La fase 1 asumió un solo escritor. Ahora hay tres caminos que escriben: el receptor del aviso, el procesamiento en segundo plano y la reconciliación. SQLite en modo por omisión responde `database is locked` ante escrituras solapadas.

Se activa `PRAGMA journal_mode = WAL` y un `busy_timeout` en cada conexión. Con un único proceso y escrituras cortas alcanza; las colisiones reales las resuelve la constraint de unicidad, no el locking.

<!-- ponytail: WAL + busy_timeout alcanzan para un proceso; si alguna vez hay varios workers, la base pasa a Postgres antes que a locking aplicativo -->

### Esquema

```
  facturas                     links_pago                   eventos_webhook
  ──────────────────           ────────────────────────     ────────────────────────
  ... (fase 1)                 id                           id
  + cobro_por_link  INTEGER    factura_id  ──► facturas     proveedor
      NOT NULL DEFAULT 0       proveedor                    aviso_id
                               referencia                   ┌ UNIQUE(proveedor, aviso_id)
  pagos                        preferencia_id               cuerpo_crudo
  ──────────────────           url                          firma_valida  INTEGER
  ... (fase 1)                 monto                        recibido_en
  + proveedor       TEXT NULL  expira_en                    procesado_en    NULL = pendiente
  + referencia_ext  TEXT NULL  creado_en                    motivo_no_proc  TEXT NULL
  + link_id         NULL ─────►cancelado_en NULL            pago_id ──► pagos  NULL
  + reconciliacion_id NULL ─┐
    ┌ UNIQUE(proveedor,     │  reconciliaciones
    │        referencia_ext)└─ ─────────────────────
    └ (varios NULL admitidos)  id, desde, hasta, ejecutada_en,
                               consultados, registrados, excepciones
```

`pagos` conserva su estructura y sus triggers de inmutabilidad; solo suma columnas anulables. Las facturas y movimientos existentes quedan válidos con `cobro_por_link = 0` y sin referencia externa.

La trazabilidad exigida por las specs sale de ahí: un movimiento originado por un aviso se alcanza desde `eventos_webhook.pago_id`, y uno originado por una reconciliación lleva `reconciliacion_id`, que apunta al período consultado.

### Cliente del proveedor aislado tras una interfaz propia

Todas las llamadas a MercadoPago (crear preferencia, cancelarla, consultar un pago, buscar pagos de un período) pasan por un módulo único. Los tests usan un doble de ese módulo y **no tocan la red**: el comportamiento a verificar es el del sistema ante respuestas del proveedor, incluidas las respuestas feas, que con la API real no se pueden provocar a voluntad.

### Ejecución de la reconciliación

Se ejecuta a demanda: un botón en la interfaz y un comando por línea de comandos con rango de fechas. Sin scheduler. Programarla es una línea de `cron` sobre el mismo comando el día que el ejercicio se despliegue, y no requiere código distinto.

## Risks / Trade-offs

- **El procesamiento en segundo plano se pierde si el proceso muere** → Aceptado por diseño: la reconciliación de respaldo recupera el pago. Es la razón por la que la reconciliación entra en esta fase y no en la siguiente.
- **MercadoPago no alcanza `localhost`** → El sandbox exige URL pública HTTPS: túnel durante el desarrollo o despliegue. Es una tarea, no un detalle de configuración.
- **Endpoint público que provoca escrituras** → La firma filtra ruido y los datos se consultan con el token propio, así que un aviso falso no puede inyectar montos. Un atacante con la URL puede, a lo sumo, generar consultas a la API.
- **Credenciales de MercadoPago** → Solo credenciales de prueba, en variables de entorno, nunca en el repositorio. Un token de producción no debe existir en el entorno de desarrollo.
- **Estados intermedios de pago** → Solo el estado confirmado registra movimiento. Un pago que se confirma después queda cubierto porque MercadoPago emite un aviso nuevo, y si ese aviso se pierde, la reconciliación lo levanta.
- **Avisos fuera de orden** → El estado se lee siempre desde la API en el momento de procesar, no desde el aviso, de modo que un aviso viejo procesado tarde consulta el estado actual y no revierte nada.
- **El cliente paga por fuera aunque haya link** → Apagar `cobro_por_link` cancela el link y rehabilita el registro manual. Sin esa salida el caso quedaría trabado.
- **Doble cobro real** (el cliente paga el link y además transfiere) → Se registra como sobrepago con saldo negativo visible, que es el comportamiento ya especificado en la fase 1. No se bloquea.
- **La bandeja solo mira** → Una excepción que la reconciliación no puede reparar exige acción humana sobre la factura. Es deliberado: ninguna pantalla de administración escribe dinero.
- **La verificación de firma quedó sin comprobar contra avisos reales** → Con credenciales de usuario de prueba, los avisos los emite la aplicación de ese usuario, cuyo secreto de firma no es accesible: el secreto disponible pertenece a otra aplicación de la cuenta. En un pago real, los cuatro avisos llegaron y los cuatro fueron rechazados por firma. **No se relajó la verificación**: aceptar avisos sin firma convertiría un endpoint público en un disparador de consultas contra la cuenta del proveedor. El dinero quedó cubierto por la reconciliación, que registró el pago correctamente. Queda por verificar con credenciales cuyo secreto de firma corresponda a la aplicación que emite los avisos.
- **La búsqueda de pagos por rango de fechas no devuelve resultados** con credenciales de usuario de prueba, ni siquiera sin filtros → La reconciliación pregunta además por la referencia de cada link cuya factura sigue con saldo, que es el camino que efectivamente recupera un aviso perdido. El barrido por período se conserva porque es el único que puede encontrar pagos sin link propio; con estas credenciales ese caso no se detecta.
- **El proveedor emite dos formatos de aviso** (`{"type":"payment","data":{"id":…}}` y `{"topic":"payment","resource":…}`) y en el segundo el campo `id` identifica al aviso, no al pago → El parseo contempla ambos; confundirlos consultaría un pago inexistente.

## Migration Plan

El esquema se amplía de forma aditiva: tablas nuevas y columnas anulables o con valor por omisión sobre las existentes. No hay que reescribir ni reinterpretar datos previos. `CREATE TABLE IF NOT EXISTS` no altera tablas ya creadas, así que las columnas nuevas sobre `facturas` y `pagos` requieren `ALTER TABLE ADD COLUMN` condicionado a su ausencia, ejecutado al iniciar.

Rollback: apagar `cobro_por_link` en todas las facturas devuelve el sistema al comportamiento de la fase 1 sin borrar nada; los movimientos ya registrados por el proveedor siguen siendo válidos.

## Open Questions

- ~~País de la cuenta de MercadoPago~~ → **resuelto**: cuenta de Chile, moneda CLP sin decimales, `DECIMALES = 0`. Confirmado contra el proveedor, que aceptó el monto como entero y devolvió `currency_id: CLP`.
- Cómo obtener el secreto de firma correspondiente a la aplicación que emite los avisos, para verificar la ruta del webhook de extremo a extremo. No cambia el diseño: el código de verificación ya está y es el mismo; solo falta un secreto que corresponda.
- Ventana por omisión de la reconciliación (último día, últimos siete días). Es un parámetro, no una decisión de diseño.
- Duración de la vigencia de la preferencia de pago. Depende de lo que permita el proveedor y de qué tan seguido se quiera regenerar.
