## 1. Entorno y credenciales

- [x] 1.1 Crear la cuenta y las credenciales de prueba de MercadoPago; dejar registrado el país elegido y fijar `DECIMALES` en `app/montos.py` según la moneda de esa cuenta
- [x] 1.2 Cargar access token de prueba y secreto de firma desde variables de entorno, con un `.env.example` sin valores reales y `.env` excluido del repositorio; la app debe fallar al arrancar con un mensaje claro si faltan
- [x] 1.3 Instalar el plugin oficial de MercadoPago para Claude Code y usarlo para confirmar contra la documentación vigente: formato del encabezado de firma y del manifiesto a firmar, campos de la respuesta de consulta de un pago, estados posibles y parámetros de búsqueda de pagos por período
- [x] 1.4 Levantar una URL pública HTTPS hacia el entorno local (túnel) y registrarla como destino de avisos en la cuenta de prueba
- [x] 1.5 Agregar la dependencia del cliente HTTP y actualizar `requirements.txt`

## 2. Esquema

- [x] 2.1 Agregar `PRAGMA journal_mode = WAL` y `busy_timeout` a la conexión
- [x] 2.2 Crear las tablas `links_pago`, `eventos_webhook` y `reconciliaciones` según `design.md`, con sus `NOT NULL` y claves foráneas
- [x] 2.3 Agregar `UNIQUE (proveedor, aviso_id)` en `eventos_webhook`
- [x] 2.4 Ampliar `facturas` con `cobro_por_link` (por omisión apagado) y `pagos` con `proveedor`, `referencia_externa`, `link_id` y `reconciliacion_id`, mediante `ALTER TABLE ADD COLUMN` condicionado a su ausencia para no romper bases existentes
- [x] 2.5 Agregar `UNIQUE (proveedor, referencia_externa)` en `pagos`, verificando que admite múltiples movimientos manuales sin referencia
- [x] 2.6 Verificar a mano que los triggers de inmutabilidad de `pagos` siguen vigentes tras la migración y que una base de la fase 1 se migra sin pérdida

## 3. Cliente de MercadoPago

- [x] 3.1 Crear el módulo único de acceso al proveedor con las operaciones: crear preferencia, cancelar preferencia, consultar pago por identificador y buscar pagos confirmados de un período
- [x] 3.2 Traducir los montos del proveedor a enteros de la unidad mínima usando exclusivamente el helper de `app/montos.py`
- [x] 3.3 Escribir un doble del módulo para los tests, capaz de simular: pago confirmado, pendiente, rechazado, reembolso total, reembolso parcial, error de red y respuesta malformada
- [x] 3.4 Verificar contra el sandbox real que crear una preferencia y consultar un pago funcionan con las credenciales de prueba

## 4. Links de pago

- [x] 4.1 Implementar la generación de link: crea la preferencia por el saldo pendiente, con la factura identificada en la referencia, y persiste URL, referencia, identificador del proveedor, monto y expiración
- [x] 4.2 Manejar la falla del proveedor al generar: no persistir link, informar el error y dejar el modo de cobro sin cambios
- [x] 4.3 Implementar el estado del link derivado (cancelado / pagado / expirado / vigente), almacenando solo `cancelado_en`
- [x] 4.4 Implementar el interruptor `cobro_por_link`: encenderlo genera link; apagarlo cancela el link vigente en el proveedor y rehabilita el registro manual
- [x] 4.5 Implementar la regeneración perezosa: al consultar el link de una factura en modo cobro por link con el vigente expirado, generar uno nuevo y conservar el anterior en el historial
- [x] 4.6 Garantizar que una factura tenga a lo sumo un link vigente a la vez
- [x] 4.7 Bloquear el registro manual de pagos cuando la factura está en modo cobro por link, sin afectar los movimientos de origen automático

## 5. Recepción de avisos

- [x] 5.1 Implementar el endpoint público de webhook: leer el cuerpo crudo, verificar la firma sobre esos bytes exactos con comparación en tiempo constante, persistir el aviso íntegro y responder de inmediato
- [x] 5.2 Persistir los avisos con firma inválida o ausente marcados como no verificados, sin procesarlos
- [x] 5.3 Persistir también los avisos de contenido ilegible, con su motivo
- [x] 5.4 Aplicar la idempotencia por aviso: un aviso repetido no crea registro ni dispara procesamiento, y la respuesta sigue siendo exitosa
- [x] 5.5 Disparar el procesamiento en segundo plano después de responder

## 6. Procesamiento de pagos

- [x] 6.1 Implementar el procesamiento: consultar el pago al proveedor por el identificador del aviso, sin usar datos del cuerpo del aviso
- [x] 6.2 Registrar movimiento solo cuando el proveedor reporte el pago confirmado; los demás estados quedan registrados sin movimiento
- [x] 6.3 Resolver la factura a partir de la referencia del pago, sin coincidencias por monto, fecha ni cliente
- [x] 6.4 Registrar el movimiento por el monto bruto, mediante `registrar_pago` con el origen del proveedor y su referencia externa; guardar comisión y neto junto al aviso
- [x] 6.5 Marcar el link como pagado y asociar el aviso al movimiento que originó
- [x] 6.6 Registrar como no procesado, con su motivo, todo aviso que falle: consulta al proveedor caída, referencia desconocida, factura inexistente
- [x] 6.7 Implementar los reembolsos y contracargos como movimientos negativos independientes con su propia referencia externa, admitiendo varios sobre un mismo pago y sin usar `revierte_a`

## 7. Conciliación

- [x] 7.1 Implementar la reconciliación de un período: consultar los pagos confirmados al proveedor y registrar los que no tengan movimiento, apoyándose en la unicidad de la referencia externa para no duplicar
- [x] 7.2 Asociar cada movimiento registrado por reconciliación a la ejecución que lo originó
- [x] 7.3 Registrar como excepción los pagos sin factura asociable encontrados durante la reconciliación
- [x] 7.4 Informar el resumen de cada ejecución: consultados, registrados y excepciones; persistirlo con el período
- [x] 7.5 Manejar la caída del proveedor durante la reconciliación sin registrar movimientos y avisando que no pudo completarse
- [x] 7.6 Exponer la ejecución por línea de comandos con rango de fechas, con ventana por omisión de los últimos siete días

## 8. Vistas

- [x] 8.1 Mostrar el modo de cobro de cada factura en el listado
- [x] 8.2 En el detalle: interruptor de cobro por link, URL del link vigente e historial de links con su estado
- [x] 8.3 En el detalle: ocultar o deshabilitar el formulario de pago manual cuando la factura está en modo cobro por link, indicando el motivo
- [x] 8.4 Mostrar el origen de cada movimiento en el historial, distinguiendo manual, proveedor y reconciliación
- [x] 8.5 Vista de bandeja de excepciones: avisos no procesados con fecha, identificador, motivo y contenido recibido, sin ninguna acción que escriba
- [x] 8.6 Botón para ejecutar la reconciliación y mostrar el resumen del resultado

## 9. Verificación

- [x] 9.1 Tests del webhook con el doble del proveedor: firma válida, firma inválida, sin firma, cuerpo ilegible y aviso repetido
- [x] 9.2 Tests de idempotencia del dinero: dos avisos distintos sobre el mismo pago producen un único movimiento; un pago ya registrado que aparece en una reconciliación no genera otro
- [x] 9.3 Tests de estados: pago confirmado registra movimiento; pendiente, rechazado y cancelado no; un pago que pasa a confirmado en un aviso posterior sí
- [x] 9.4 Test de comisión: una factura cubierta por un pago con comisión queda con saldo cero y la comisión no altera el saldo
- [x] 9.5 Tests de reembolsos: reembolso total devuelve el saldo previo, dos reembolsos parciales sobre el mismo pago conviven, un reembolso repetido registra un solo movimiento
- [x] 9.6 Tests de links: regeneración al expirar, un solo link vigente, apagar el interruptor cancela y rehabilita el pago manual
- [x] 9.7 Test de bloqueo: el pago manual se rechaza con la factura en modo cobro por link y se acepta tras apagarlo
- [x] 9.8 Tests de reconciliación: recupera un pago cuyo aviso nunca llegó, no duplica los ya registrados, y deja como excepción el pago sin factura asociable
- [x] 9.9 Verificar que la suite completa de la fase 1 sigue pasando sin cambios

## 10. Prueba de extremo a extremo en sandbox

> **10.2 y 10.4 quedan sin verificar, por decisión explícita.** Con credenciales
> de usuario de prueba, los avisos los emite una aplicación cuyo secreto de firma
> no es accesible, así que todo aviso real se rechaza por firma y nunca llega a
> convertirse en movimiento ni a producir el motivo "referencia desconocida".
> No se relajó la verificación de firma para forzar el paso. El dinero quedó
> cubierto: en el pago real, la reconciliación registró el movimiento (10.3).
> Ver `design.md` - Risks / Trade-offs.

- [x] 10.1 Crear una factura, encender el cobro por link y pagarla en el sandbox de MercadoPago con un usuario de prueba
- [ ] 10.2 Confirmar que el aviso llega por el túnel, que el movimiento se registra solo y que la factura queda pagada sin intervención
- [x] 10.3 Provocar un webhook perdido (con el servicio detenido durante el pago) y comprobar que la reconciliación lo recupera
- [ ] 10.4 Provocar una excepción (aviso con referencia desconocida) y comprobar que aparece en la bandeja sin alterar ningún saldo
- [x] 10.5 Actualizar el `README.md`: variables de entorno requeridas, cómo levantar el túnel, cómo ejecutar la reconciliación y qué hace la bandeja
