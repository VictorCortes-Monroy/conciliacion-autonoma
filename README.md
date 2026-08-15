# Libreta de facturas · conciliación autónoma

Saber **quién pagó y quién no**, sin conciliación manual y sin lógica pesada.

Una factura se cobra de dos maneras: a mano, cuando el cliente transfiere por
fuera y alguien lo registra; o **por link**, cuando el cliente paga con
MercadoPago y el estado de la factura cambia solo.

```
Python 3.14 · FastAPI · SQLite · sin ORM · sin build de frontend · 50 pruebas
```

---

## El problema

La conciliación bancaria duele por una sola razón: **el dinero llega despegado
de su causa**.

```
   CARTOLA DEL BANCO                        FACTURAS EMITIDAS
 ┌───────────────────────────┐         ┌────────────────────────────┐
 │ 14/08  $150.000  "JUAN P" │         │ FAC-001  $150.000  Cliente A│
 │ 14/08  $150.000  "TRANSF" │         │ FAC-002  $150.000  Cliente B│
 │ 15/08  $300.000  "EMPRESA"│         │ FAC-003  $150.000  Cliente A│
 └───────────────────────────┘         └────────────────────────────┘
              │                                       ▲
              └──────────── ¿cuál con cuál? ──────────┘
                              │
                    aquí vive TODA la lógica pesada
```

Montos que coinciden, nombres que no coinciden nunca, un pago que cubre tres
facturas, el cliente que transfiere $149.990 porque le cobraron comisión.

## La solución: no resolver el matching, evitarlo

Un **link único por factura** hace que el pago llegue ya etiquetado. No hay que
adivinar quién pagó: el pago nació sabiendo a qué factura pertenece.

```
   CON MATCHING (pesado)              CON REFERENCIA ÚNICA (liviano)
   ─────────────────────              ──────────────────────────────
   pago ──?──> factura                /pay/fac-1-3 ──> factura 1
   heurísticas, umbrales,             cero heurística
   revisión humana, falsos            el ID viaja con el dinero
   positivos
```

Ese es el movimiento de diseño central. Todo lo demás es consecuencia.

---

## Cómo funciona

```
   ┌──────────┐   1. crear link      ┌──────────────┐
   │  Libreta │─────────────────────▶│ MercadoPago  │
   │          │◀─── url + ref ───────│              │
   └──────────┘                      └──────────────┘
        │                                    ▲
        │ 2. le mandás la url al cliente     │ 3. el cliente paga
        ▼                                    │
   ┌──────────┐                              │
   │ Cliente  │──────────────────────────────┘
   └──────────┘                              │
                              4. webhook     ▼
   ┌─────────────────────────────────────────────────────────┐
   │  persistir aviso crudo  →  responder 200  →  procesar    │
   │                                              │           │
   │                         consultar el pago ───┤           │
   │                                              ▼           │
   │                                        registrar_pago    │
   └─────────────────────────────────────────────────────────┘
                              │
                   ┌──────────┴──────────┐
                   ▼                     ▼
            factura PAGADA        BANDEJA DE EXCEPCIONES
                                  (lo que no se pudo procesar)
                                          │
                                  RECONCILIACIÓN
                                  (lo repara sola)
```

### Estado derivado, nunca almacenado

No existe una columna `pagada` ni `estado`. Un dato que no existe no se puede
desincronizar:

```
   saldo = monto − Σ movimientos

   ┌──────────────┬────────────────┬─────────────┐
   │ saldo = monto│ 0 < saldo <    │  saldo ≤ 0  │
   │              │      monto     │             │
   │  PENDIENTE   │    PARCIAL     │   PAGADA    │
   └──────────────┴────────────────┴─────────────┘
                          ×
              fecha_venc < hoy  →  VENCIDA
```

Dos ejes independientes: una factura puede estar `PARCIAL` **y** vencida a la
vez, que es justo el caso que más interesa ver.

### Un único punto de escritura

```
   marca manual ──┐
   webhook ───────┼──▶ registrar_pago(factura, monto, fecha, origen) ──▶ pagos
   reconciliación ┘
```

La fase 2 no tuvo que tocar el modelo de saldos: `registrar_pago` recibió una
fuente más y el núcleo quedó igual.

---

## Reglas que la base de datos hace cumplir

No son convenciones de equipo: son constraints. Una regla que solo vive en el
código se olvida el día que alguien abre la base con un cliente SQL.

| Regla | Cómo se garantiza |
|---|---|
| Los montos nunca son punto flotante | `INTEGER` en la unidad mínima |
| El historial no se edita ni se borra | triggers `RAISE(ABORT)` en `UPDATE`/`DELETE` |
| Un pago externo se registra una sola vez | `UNIQUE (proveedor, referencia_externa)` |
| Un aviso repetido no se reprocesa | `UNIQUE (proveedor, aviso_id)` |
| Un movimiento se revierte una sola vez | `UNIQUE (revierte_a)` |
| Los montos son positivos | `CHECK (monto > 0)` |

Una corrección nunca es un `UPDATE`: es un movimiento más. Un reembolso es un
movimiento negativo propio, no una reversión — el proveedor admite varios
reembolsos parciales sobre un mismo pago.

### La comisión no toca el saldo

```
   El cliente paga     $150.000   →  esto salda la factura
   MercadoPago cobra   $  5.700   →  dato del evento
   A vos te llega      $144.300   →  no participa del saldo
```

Registrar el neto dejaría toda factura cobrada por link con una deuda fantasma
igual a la comisión — y de vuelta a la conciliación manual.

---

## Arrancar

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # completar credenciales de prueba
.venv/bin/uvicorn app.main:app --reload
```

En http://127.0.0.1:8000. La base (`libreta.db`) se crea sola al arrancar.

### Variables de entorno

| Variable | Para qué |
|---|---|
| `MP_ACCESS_TOKEN` | token **de prueba** de MercadoPago |
| `MP_WEBHOOK_SECRET` | secreto con el que se verifica la firma de los avisos |
| `MP_URL_PUBLICA` | URL pública HTTPS donde MercadoPago envía los avisos |
| `MP_HORAS_VIGENCIA` | horas de vida de cada link (opcional, 24) |
| `MP_SANDBOX` | `1` solo con credenciales `TEST-` de Checkout API |
| `MP_BASE_URL` | redirige la API del proveedor (opcional, pruebas locales) |

La app **no arranca** si falta alguna de las tres primeras, y dice cuál.
`.env` está fuera del repositorio.

### URL pública

MercadoPago no puede llamar a `localhost`:

```bash
cloudflared tunnel --url http://localhost:8000     # o: ngrok http 8000
```

Esa URL va en `MP_URL_PUBLICA` y se registra como destino de avisos. El
endpoint es `POST /webhooks/mercadopago`.

### Reconciliación

El webhook se puede perder. Esto le pregunta a MercadoPago qué pagos hubo y
registra los que falten:

```bash
.venv/bin/python -m app.reconciliar --dias 7
```

También hay un botón en `/excepciones`. Programarla es una línea de `cron`.

---

## Probar

```bash
.venv/bin/python test_saldos.py    # 13 · saldos, estados, vencimiento, montos
.venv/bin/python test_cobros.py    # 37 · links, avisos, reembolsos, conciliación
```

Sin frameworks, sin fixtures: `assert` y un doble del proveedor que **no toca la
red**. Lo que interesa verificar es cómo reacciona el sistema a las respuestas
feas del proveedor, que contra la API real no se pueden provocar a voluntad.

---

## Estructura

```
app/montos.py       parseo y formateo (enteros de la unidad mínima, sin float)
app/db.py           esquema, migración y dominio
app/mercadopago.py  único punto de contacto con el proveedor
app/cobros.py       links, procesamiento de avisos y reconciliación
app/main.py         rutas, formularios y webhook
app/reconciliar.py  reconciliación por línea de comandos
openspec/specs/     el contrato de comportamiento, 33 requisitos
openspec/changes/   cómo se llegó hasta acá
```

---

## Desarrollado con specs primero

Cada fase se planificó con [OpenSpec](https://github.com/Fission-AI/OpenSpec)
antes de escribir código: propuesta → especificación → diseño → tareas.

```
openspec/specs/
  facturas/spec.md        8 requisitos    creación, saldo, estados, agregados
  pagos/spec.md           9 requisitos    parciales, reversión, idempotencia
  links-pago/spec.md      4 requisitos    ciclo de vida del link
  webhooks-pago/spec.md   8 requisitos    firma, idempotencia, traducción
  conciliacion/spec.md    4 requisitos    bandeja y reconciliación
```

Los requisitos están en formato `WHEN/THEN`: cada escenario es un caso de prueba
potencial, y varios lo son de verdad.

---

## Lo que enseñó el primer pago real

Cinco bugs que **ninguna prueba con doble podía encontrar**, porque el doble
devuelve lo que uno le pide:

| Bug | Consecuencia si no se detecta |
|---|---|
| `init_point` vs `sandbox_init_point` | el checkout entra en bucle de redirecciones |
| Dos formatos de aviso, uno sin parsear | el `id` del aviso se confunde con el del pago |
| Búsqueda por fechas devolvía vacío | la reconciliación no recuperaba nada |
| Sin bundle de CAs | toda llamada a la API falla por TLS |
| La firma no se persistía | un rechazo por firma era indiagnosticable |

Y uno que sí cazó una prueba, antes de llegar a producción: `expira_en` se
guardaba en ISO (`T`) y `datetime('now')` de SQLite usa espacio. Comparados como
texto, `'T'` > `' '` — **un link vencido se veía como vigente**.

### La red de seguridad se ganó el sueldo

En el pago real, los cuatro webhooks fueron rechazados por firma. Quien registró
el pago fue la **reconciliación de respaldo**. Sin ella, la factura seguiría
diciendo que deben $150.000.

---

## Límites conocidos

Con credenciales de **usuario de prueba** de MercadoPago:

- Los avisos los emite la aplicación de ese usuario, cuyo secreto de firma no es
  accesible desde el panel de la aplicación propia: todo aviso real se rechaza
  por firma y aparece en la bandeja. **No se relajó la verificación** — aceptar
  avisos sin firma dejaría un endpoint público disponible para que cualquiera
  dispare consultas contra la cuenta del proveedor. El dinero queda cubierto por
  la reconciliación.
- La búsqueda de pagos por rango de fechas devuelve vacío incluso sin filtros,
  así que la reconciliación pregunta además por la referencia de cada link con
  saldo pendiente. Con estas credenciales no se detectan pagos sin link propio.

Fuera de alcance por decisión, no por olvido: matching de cartola bancaria, un
pago que cubre varias facturas, autenticación de usuarios y cualquier producto
de MercadoPago distinto de checkout con preferencia.

---

## Decisiones de diseño

Están documentadas con su alternativa descartada y el porqué:

- [`openspec/changes/archive/2026-08-14-add-libreta-facturas/design.md`](openspec/changes/archive/2026-08-14-add-libreta-facturas/design.md)
- [`openspec/changes/archive/2026-08-15-add-conciliacion-mercadopago/design.md`](openspec/changes/archive/2026-08-15-add-conciliacion-mercadopago/design.md)

Las dos que explican el resto:

**El aviso es un timbre, no una carta.** MercadoPago notifica con un
identificador; el monto y el estado se consultan a su API con el token propio.
Nada se registra con datos tomados del cuerpo del aviso, así que un aviso
falsificado no puede inyectar montos.

**Recibir y procesar están separados.** El webhook persiste el aviso y responde;
el trabajo ocurre después. Consultar la API dentro del request haría que un
proveedor lento provocara timeouts y, con ellos, más reintentos. Si ese trabajo
se pierde, la reconciliación lo recupera — por eso no hace falta una cola.
