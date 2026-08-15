## Purpose

Recibir los avisos de pago del proveedor y convertirlos en movimientos contra la factura correcta, exactamente una vez, resistiendo reintentos, avisos falsificados, avisos fuera de orden y caídas del proveedor.

## ADDED Requirements

### Requirement: Recepción de avisos

El sistema SHALL exponer un endpoint público que reciba los avisos del proveedor. El aviso SHALL persistirse íntegro tal como se recibió, junto con el momento de recepción, antes de cualquier intento de interpretarlo. El endpoint SHALL responder de forma exitosa apenas el aviso quede persistido, sin esperar a su procesamiento.

#### Scenario: Aviso recibido

- **WHEN** el proveedor envía un aviso
- **THEN** el contenido íntegro del aviso queda persistido con su fecha de recepción
- **AND** el sistema responde de forma exitosa

#### Scenario: El procesamiento falla

- **WHEN** un aviso queda persistido pero su procesamiento posterior falla
- **THEN** la respuesta al proveedor ya fue exitosa
- **AND** el aviso queda registrado como no procesado con el motivo de la falla

#### Scenario: Aviso con contenido ilegible

- **WHEN** llega un aviso cuyo contenido no puede interpretarse
- **THEN** el aviso queda persistido igualmente
- **AND** queda registrado como no procesado con el motivo correspondiente

### Requirement: Verificación de autenticidad

El sistema SHALL verificar la firma de cada aviso recibido usando el secreto compartido con el proveedor. Los avisos con firma inválida o ausente SHALL persistirse marcados como no verificados y NO SHALL procesarse. La verificación SHALL realizarse sobre el contenido exacto recibido.

#### Scenario: Firma válida

- **WHEN** llega un aviso cuya firma corresponde al contenido recibido
- **THEN** el aviso queda marcado como verificado y pasa a procesamiento

#### Scenario: Firma inválida

- **WHEN** llega un aviso cuya firma no corresponde al contenido recibido
- **THEN** el aviso queda persistido y marcado como no verificado
- **AND** no se registra ningún movimiento
- **AND** el aviso aparece entre los avisos no procesados con el motivo correspondiente

#### Scenario: Aviso sin firma

- **WHEN** llega un aviso sin firma
- **THEN** recibe el mismo tratamiento que un aviso con firma inválida

### Requirement: El aviso no es fuente de datos del pago

El aviso del proveedor SHALL tratarse únicamente como notificación de que algo ocurrió. El monto, el estado y la referencia del pago SHALL obtenerse consultando al proveedor por el identificador informado en el aviso. El sistema NO SHALL registrar movimientos con datos tomados del cuerpo del aviso.

#### Scenario: Datos obtenidos del proveedor

- **WHEN** se procesa un aviso verificado
- **THEN** el sistema consulta al proveedor el pago identificado en el aviso
- **AND** el monto, el estado y la referencia usados provienen de esa consulta

#### Scenario: El proveedor no responde la consulta

- **WHEN** la consulta al proveedor falla
- **THEN** no se registra ningún movimiento
- **AND** el aviso queda registrado como no procesado con el motivo correspondiente

### Requirement: Solo los pagos confirmados mueven el saldo

El sistema SHALL registrar un movimiento únicamente cuando el proveedor reporte el pago como confirmado o capturado. Los pagos en estado pendiente, en proceso, rechazado o cancelado SHALL quedar registrados como avisos procesados sin movimiento y NO SHALL alterar el saldo de la factura.

#### Scenario: Pago confirmado

- **WHEN** el pago consultado al proveedor está confirmado
- **THEN** se registra un movimiento contra la factura correspondiente
- **AND** el saldo de la factura disminuye en el monto del pago

#### Scenario: Pago pendiente o rechazado

- **WHEN** el pago consultado al proveedor está pendiente, en proceso, rechazado o cancelado
- **THEN** no se registra ningún movimiento
- **AND** el saldo de la factura no cambia
- **AND** el aviso queda registrado con el estado informado por el proveedor

#### Scenario: Un pago que se confirma después

- **WHEN** un pago que antes estaba pendiente llega confirmado en un aviso posterior
- **THEN** se registra el movimiento en ese momento

### Requirement: Idempotencia de los avisos

Los avisos SHALL identificarse de forma única por proveedor e identificador de aviso. Un aviso ya recibido NO SHALL persistirse ni procesarse de nuevo, y el sistema SHALL responder de forma exitosa igualmente.

#### Scenario: Reintento del mismo aviso

- **WHEN** el proveedor reenvía un aviso ya recibido
- **THEN** no se crea un registro adicional
- **AND** no se ejecuta un procesamiento adicional
- **AND** el sistema responde de forma exitosa

### Requirement: Idempotencia del dinero

Un pago del proveedor SHALL producir a lo sumo un movimiento, identificado por proveedor y referencia externa del pago. Varios avisos distintos referidos al mismo pago SHALL producir un único movimiento.

#### Scenario: Varios avisos sobre el mismo pago

- **WHEN** el proveedor envía dos avisos distintos que refieren al mismo pago confirmado
- **THEN** se registra un único movimiento
- **AND** el saldo de la factura refleja ese pago una sola vez

#### Scenario: Reconciliación posterior al aviso

- **WHEN** un pago ya registrado a partir de un aviso aparece luego en una reconciliación
- **THEN** no se registra un movimiento adicional

### Requirement: Monto bruto y comisión

El movimiento SHALL registrarse por el monto que pagó el cliente, sin descontar la comisión del proveedor. La comisión y el monto neto informados por el proveedor SHALL conservarse asociados al aviso y NO SHALL afectar el saldo de la factura.

#### Scenario: Pago con comisión

- **WHEN** se registra un pago confirmado sobre el que el proveedor cobró comisión
- **THEN** el movimiento queda por el monto que pagó el cliente
- **AND** la factura cubierta por completo queda con saldo cero
- **AND** la comisión y el monto neto quedan registrados junto al aviso

### Requirement: Pago que no corresponde a ninguna factura

Cuando la referencia de un pago no corresponda a ningún link generado por el sistema, o la factura asociada no exista, el sistema NO SHALL registrar movimiento alguno y SHALL dejar el aviso registrado como no procesado con ese motivo.

#### Scenario: Referencia desconocida

- **WHEN** se procesa un pago confirmado cuya referencia no corresponde a ningún link del sistema
- **THEN** no se registra ningún movimiento
- **AND** el aviso queda registrado como no procesado con el motivo correspondiente
