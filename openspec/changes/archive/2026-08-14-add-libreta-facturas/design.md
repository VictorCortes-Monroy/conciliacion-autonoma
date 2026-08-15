## Context

Proyecto nuevo, repositorio vacío. Ver `proposal.md` para la motivación y `specs/` para los requisitos.

Restricciones que moldean el diseño:

- Es un ejercicio de una sola persona, sin usuarios concurrentes reales ni despliegue.
- Los datos son financieros: montos, historial y trazabilidad no admiten atajos.
- Las specs exigen que saldo, estado de cobro y vencimiento sean **derivados**, nunca almacenados.
- Etapa siguiente previsible: incorporar fuentes automáticas de pago (importación de cartola, webhook). El diseño de hoy no debe obligar a reescribir para eso.

## Goals / Non-Goals

**Goals:**

- Modelo de datos donde el saldo no pueda desincronizarse de los pagos, por construcción y no por disciplina.
- Invariantes financieras expresadas como constraints de base de datos, no solo como validación en código.
- Mínima cantidad de piezas móviles: un proceso, un archivo de base de datos, sin build de frontend.
- Una sola función de entrada para registrar movimientos, de modo que una fuente automática futura sea un argumento distinto y no un camino nuevo.

**Non-Goals:**

- Rendimiento a escala: los agregados se recalculan en cada consulta.
- Concurrencia real: un solo escritor, sin locking aplicativo.
- Portabilidad de base de datos: el esquema usa SQLite deliberadamente.

## Decisions

### Stack: FastAPI + Jinja + SQLite, sin ORM

`sqlite3` viene en la biblioteca estándar de Python y el sistema tiene dos tablas y menos de diez consultas. Un ORM agregaría una capa de traducción sobre SQL que igual habría que leer para entender las constraints.

*Alternativas:* Next.js + Postgres da `NUMERIC(12,2)` nativo y despliegue fácil, pero suma servidor de base de datos, build de frontend y migraciones para un ejercicio local. SQLModel/SQLAlchemy sobre SQLite se descarta porque a dos tablas no compensa. Si el proyecto crece hacia varios usuarios o despliegue, migrar a Postgres es el primer movimiento.

### Montos como `INTEGER` en la unidad mínima

SQLite no tiene tipo decimal. Los montos se guardan como enteros de la unidad mínima (centavos) y se formatean solo al mostrar, en un único helper.

*Alternativas:* `REAL` queda descartado sin discusión — el requisito de representación exacta de montos falla con punto flotante. `TEXT` con decimal parseado en Python conserva precisión pero rompe `SUM()` en SQL, que es justamente donde se calcula el saldo.

### Tabla `pagos` append-only en vez de columna de estado

```
  facturas                          pagos
  ─────────────────────────         ──────────────────────────────
  id            INTEGER PK          id             INTEGER PK
  cliente       TEXT NOT NULL       factura_id     INTEGER NOT NULL → facturas(id)
  monto         INTEGER NOT NULL    monto          INTEGER NOT NULL
                CHECK (monto > 0)                  CHECK (monto <> 0)
  fecha_venc    TEXT NOT NULL       fecha          TEXT NOT NULL
  creada_en     TEXT NOT NULL       origen         TEXT NOT NULL DEFAULT 'manual'
                                    registrado_en  TEXT NOT NULL
                                    revierte_a     INTEGER NULL → pagos(id)
```

No existe columna `pagada` ni `estado` en `facturas`. El saldo es `monto - COALESCE(SUM(pagos.monto), 0)` y el estado sale de comparar ese saldo contra el monto. Un dato que no existe no se puede desincronizar.

`revierte_a` apunta al movimiento que una reversión anula: permite mostrar el par en el historial y evita revertir dos veces el mismo pago, sin necesidad de borrar nada.

*Alternativas:* `facturas.pagada_en TIMESTAMP NULL` es una columna en vez de una tabla, pero no soporta pagos parciales ni deja rastro de quién cambió qué. Una columna `saldo` materializada obliga a mantener sincronía en cada escritura, que es exactamente la clase de error que este esquema evita.

### Inmutabilidad forzada por triggers, no por convención

```sql
CREATE TRIGGER pagos_no_update BEFORE UPDATE ON pagos
  BEGIN SELECT RAISE(ABORT, 'movimientos inmutables'); END;
CREATE TRIGGER pagos_no_delete BEFORE DELETE ON pagos
  BEGIN SELECT RAISE(ABORT, 'movimientos inmutables'); END;
```

Cuatro líneas que convierten "no editamos el historial" de acuerdo de equipo en imposibilidad. Una regla que solo vive en el código de la aplicación se olvida el día que alguien abre la base con un cliente SQL.

Las claves foráneas requieren `PRAGMA foreign_keys = ON` en cada conexión: SQLite las ignora por defecto. Sin ese pragma, la constraint del esquema es decorativa.

### Estado y vencimiento derivados en la consulta

Ambos ejes se calculan en SQL al leer, en una vista o en el `SELECT` del listado. No hay job nocturno que marque vencidos ni columna que actualizar: `fecha_venc < date('now')` es la definición completa de vencido.

Las fechas se guardan como `TEXT` en formato ISO `YYYY-MM-DD`, que en SQLite ordena y compara correctamente como cadena. Sin zonas horarias: la fecha de vencimiento es una fecha de calendario, no un instante.

<!-- ponytail: los agregados recorren todos los pagos en cada consulta; indexar pagos(factura_id) y, si alguna vez importa, materializar el saldo con trigger -->

### Punto único de escritura

```
  formulario "Registrar pago" ──┐
                                ├──> registrar_pago(factura_id, monto, fecha, origen)
  botón "Revertir"  ────────────┘         │
                                          └──> INSERT en pagos
  (futuro: cartola, webhook) ──────────────┘
```

Revertir no es una operación distinta: es `registrar_pago` con monto negativo y `revierte_a` apuntando al original. Una sola función escribe en `pagos`, y una fuente automática futura solo cambia el argumento `origen`.

### Estructura de archivos

```
  app/
    main.py        rutas FastAPI + registrar_pago
    db.py          conexión, PRAGMA, schema inicial idempotente
    templates/
      listado.html
      detalle.html
  test_saldos.py   asserts sobre saldo, estados, parciales, reversión
```

La vista agregada (total adeudado, total vencido, deuda por cliente) va en el encabezado del listado, no como una tercera vista: son cuatro consultas sobre los mismos datos que la página ya muestra.

## Risks / Trade-offs

- **Dos pagos registrados a la vez podrían sobrepasar el monto** → No requiere mitigación: el sobrepago ya está permitido por spec y queda visible como saldo negativo. La ausencia de lock no produce un estado inválido.
- **Formatear centavos mal en la vista muestra montos 100× equivocados** → Un único helper de formateo, cubierto por el test.
- **Sin autenticación, cualquiera con acceso a la red puede escribir** → La app corre en `localhost` y no se expone. Si alguna vez se despliega, autenticación es prerrequisito, no mejora.
- **Los triggers de inmutabilidad bloquean también correcciones legítimas de tipeo** → Es el comportamiento buscado: un monto mal ingresado se corrige con un movimiento de reversión más uno nuevo, que es lo que un registro contable exige.
- **SQLite no valida `Σ pagos ≤ monto`** → Deliberado: el sobrepago es dato permitido, no error.
- **Migrar a Postgres implica reescribir tipos y triggers** → Aceptado; solo se paga si el proyecto deja de ser un ejercicio local.

## Migration Plan

No hay sistema previo ni datos que migrar. El esquema se crea con `CREATE TABLE IF NOT EXISTS` al iniciar la app, de modo que arrancar contra un archivo inexistente lo inicializa. Rollback = borrar el archivo `.db`.

## Open Questions

- Moneda y cantidad de decimales a mostrar (dos decimales o entero sin decimales). No afecta el esquema: en ambos casos se guardan enteros de la unidad mínima y solo cambia el helper de formateo.
- Si el listado necesita filtros u ordenamiento por estado. Se puede agregar sobre las mismas consultas sin tocar el modelo.
