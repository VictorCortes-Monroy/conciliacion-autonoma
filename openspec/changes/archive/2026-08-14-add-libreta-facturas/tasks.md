## 1. Base y esquema

- [x] 1.1 Crear el proyecto: `app/`, dependencias (`fastapi`, `uvicorn`, `jinja2`) y arranque mínimo que responda en `/`
- [x] 1.2 En `app/db.py`, abrir la conexión SQLite aplicando `PRAGMA foreign_keys = ON` en cada conexión
- [x] 1.3 Crear el esquema idempotente (`CREATE TABLE IF NOT EXISTS`) con las tablas `facturas` y `pagos` según `design.md`, incluyendo los `CHECK (monto > 0)`, `CHECK (monto <> 0)`, `NOT NULL` y la clave foránea `pagos.factura_id`
- [x] 1.4 Agregar los triggers `pagos_no_update` y `pagos_no_delete` que abortan con `RAISE(ABORT, ...)`
- [x] 1.5 Agregar índice sobre `pagos(factura_id)`
- [x] 1.6 Verificar a mano que un `UPDATE` y un `DELETE` sobre `pagos` fallan, y que un `INSERT` con `factura_id` inexistente también

## 2. Núcleo de dominio

- [x] 2.1 Implementar el helper de montos: parseo de monto ingresado a entero de centavos y formateo de centavos a texto para mostrar
- [x] 2.2 Implementar `crear_factura(cliente, monto, fecha_venc)` validando los tres campos obligatorios y monto mayor a cero
- [x] 2.3 Implementar `registrar_pago(factura_id, monto, fecha, origen='manual', revierte_a=None)` como único punto de escritura sobre `pagos`, rechazando monto cero y factura inexistente
- [x] 2.4 Implementar `revertir_pago(pago_id)` sobre `registrar_pago`, con monto negativo equivalente y `revierte_a` apuntando al movimiento original; rechazar si ese pago ya fue revertido
- [x] 2.5 Implementar la consulta de facturas con saldo, estado de cobro (`PENDIENTE`/`PARCIAL`/`PAGADA`) y condición de vencimiento derivados en SQL, más los días transcurridos desde el vencimiento
- [x] 2.6 Implementar la consulta de detalle: datos de la factura más su historial de movimientos en orden cronológico, con monto, fecha y origen
- [x] 2.7 Implementar las consultas agregadas: total adeudado, total vencido y deuda por cliente

## 3. Verificación del núcleo

- [x] 3.1 Escribir `test_saldos.py` con asserts sobre base en memoria, sin framework de fixtures
- [x] 3.2 Cubrir: saldo sin pagos, pago parcial deja `PARCIAL`, pagos parciales que suman el total dejan saldo exactamente cero y estado `PAGADA`
- [x] 3.3 Cubrir: sobrepago deja saldo negativo y estado `PAGADA`; reversión devuelve el saldo al valor previo y ambos movimientos siguen en el historial
- [x] 3.4 Cubrir: factura con saldo positivo y fecha pasada aparece vencida; la misma factura pagada deja de aparecer vencida; una factura `PARCIAL` y vencida aparece en ambos ejes
- [x] 3.5 Cubrir: ida y vuelta del helper de montos (texto → centavos → texto) sin pérdida

## 4. Vistas

- [x] 4.1 Vista de listado: tabla de facturas con cliente, monto, fecha de vencimiento, saldo, estado de cobro y marca de vencida con días de atraso
- [x] 4.2 Encabezado del listado con los agregados: total adeudado, total vencido y deuda por cliente
- [x] 4.3 Formulario de creación de factura en el listado, mostrando el mensaje de error correspondiente cuando la validación falla
- [x] 4.4 Vista de detalle: datos de la factura, saldo, estado, condición de vencimiento e historial completo de movimientos con su origen
- [x] 4.5 Formulario de registro de pago en el detalle, con el monto precargado con el saldo pendiente y editable antes de confirmar
- [x] 4.6 Acción de revertir junto a cada movimiento del historial, deshabilitada para los movimientos ya revertidos y para las reversiones mismas

## 5. Cierre

- [x] 5.1 Recorrer el flujo completo a mano: crear factura, abonar parcial, verificar `PARCIAL`, completar el pago, verificar `PAGADA`, revertir y verificar que el saldo vuelve
- [x] 5.2 Confirmar que ningún monto se muestra con error de escala (centavos mostrados como unidades o al revés) en listado, detalle y agregados
- [x] 5.3 Escribir un `README.md` breve: cómo levantar la app y cómo correr el test
