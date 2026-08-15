## Purpose

Dar visibilidad a todo lo que el cobro automático no pudo resolver por sí solo y recuperar los pagos cuyo aviso nunca llegó, de modo que la libreta no mienta en silencio cuando el camino automático falla.

## Requirements

### Requirement: Bandeja de excepciones

El sistema SHALL exponer una vista con los avisos recibidos que no produjeron movimiento, mostrando para cada uno su fecha de recepción, el identificador informado por el proveedor, el motivo por el cual no se procesó y el contenido recibido. La bandeja SHALL ser de solo lectura: NO SHALL ofrecer acciones que registren, modifiquen o reprocesen movimientos.

#### Scenario: Consulta de la bandeja

- **WHEN** el usuario abre la bandeja de excepciones
- **THEN** se listan los avisos recibidos que no produjeron movimiento, con su fecha, su identificador y el motivo

#### Scenario: Motivos distinguibles

- **WHEN** la bandeja contiene avisos que fallaron por firma inválida, por referencia desconocida y por falla al consultar al proveedor
- **THEN** cada uno muestra su propio motivo, distinguible de los demás

#### Scenario: La bandeja no ejecuta acciones

- **WHEN** el usuario consulta un aviso de la bandeja
- **THEN** no se ofrece ninguna acción que registre, modifique o reprocese movimientos

#### Scenario: Aviso resuelto por otra vía

- **WHEN** un aviso de la bandeja corresponde a un pago que luego se registra por reconciliación
- **THEN** el aviso deja de figurar como pendiente en la bandeja

### Requirement: Reconciliación de respaldo

El sistema SHALL permitir ejecutar una reconciliación que consulte al proveedor los pagos confirmados de un período y registre los que aún no tengan movimiento. La reconciliación SHALL ser el mecanismo automático de recuperación cuando un aviso se pierde o no puede procesarse.

#### Scenario: Pago cuyo aviso nunca llegó

- **WHEN** se ejecuta la reconciliación de un período en el que el proveedor registra un pago confirmado sin movimiento en el sistema
- **THEN** se registra el movimiento correspondiente contra la factura de ese pago
- **AND** el saldo de la factura se actualiza

#### Scenario: Pagos ya registrados

- **WHEN** la reconciliación encuentra pagos confirmados que ya tienen movimiento
- **THEN** no se registran movimientos adicionales
- **AND** los saldos no cambian

#### Scenario: Pago sin factura asociable

- **WHEN** la reconciliación encuentra un pago confirmado cuya referencia no corresponde a ningún link del sistema
- **THEN** no se registra ningún movimiento
- **AND** el hallazgo queda visible entre las excepciones con ese motivo

#### Scenario: El proveedor no responde

- **WHEN** la consulta al proveedor falla durante la reconciliación
- **THEN** no se registra ningún movimiento
- **AND** el sistema informa que la reconciliación no pudo completarse

### Requirement: Trazabilidad del origen de cada movimiento automático

Todo movimiento registrado por un aviso o por una reconciliación SHALL quedar asociado al aviso o a la ejecución que lo originó, de modo que sea posible reconstruir por qué el saldo de una factura cambió sin intervención humana.

#### Scenario: Movimiento originado por un aviso

- **WHEN** el usuario consulta un movimiento registrado a partir de un aviso
- **THEN** puede identificarse el aviso que lo originó

#### Scenario: Movimiento originado por una reconciliación

- **WHEN** el usuario consulta un movimiento registrado por una reconciliación
- **THEN** puede identificarse que su origen fue una reconciliación y de qué período

### Requirement: Resumen de conciliación

El sistema SHALL exponer, tras cada ejecución de reconciliación, cuántos pagos fueron consultados, cuántos movimientos se registraron y cuántos hallazgos quedaron como excepción.

#### Scenario: Resultado de una ejecución

- **WHEN** termina una ejecución de reconciliación
- **THEN** se informa la cantidad de pagos consultados, la de movimientos registrados y la de excepciones
