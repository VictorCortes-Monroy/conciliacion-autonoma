## MODIFIED Requirements

### Requirement: Registro de pagos

El sistema SHALL permitir registrar un pago contra una factura existente, con monto, fecha del pago y origen. El monto SHALL ser distinto de cero. El pago SHALL quedar asociado a una única factura. El registro manual de pagos desde la interfaz SHALL estar disponible únicamente cuando la factura no esté en modo cobro por link; los movimientos de origen automático SHALL registrarse con independencia de ese modo.

#### Scenario: Pago registrado correctamente

- **WHEN** el usuario registra un pago con monto distinto de cero contra una factura existente que no está en modo cobro por link
- **THEN** el movimiento queda persistido con su monto, su fecha, su origen y la fecha en que fue registrado
- **AND** el saldo de la factura disminuye en el monto del pago

#### Scenario: Monto cero

- **WHEN** el usuario intenta registrar un pago de monto cero
- **THEN** el sistema rechaza la operación
- **AND** no se registra ningún movimiento

#### Scenario: Factura inexistente

- **WHEN** el usuario intenta registrar un pago contra una factura que no existe
- **THEN** el sistema rechaza la operación
- **AND** no se registra ningún movimiento

#### Scenario: Factura en modo cobro por link

- **WHEN** el usuario intenta registrar un pago manual contra una factura en modo cobro por link
- **THEN** el sistema rechaza la operación e informa que el cobro de esa factura es por link
- **AND** no se registra ningún movimiento

#### Scenario: Movimiento automático sobre una factura en modo cobro por link

- **WHEN** se registra un pago de origen automático contra una factura en modo cobro por link
- **THEN** el movimiento queda persistido normalmente
- **AND** el saldo de la factura disminuye en el monto del pago

### Requirement: Origen del pago identificado

Todo movimiento de pago SHALL registrar su origen. Los movimientos ingresados desde la interfaz SHALL quedar con origen `manual`; los originados por un aviso del proveedor o por una reconciliación SHALL quedar con el origen que identifica a ese proveedor. El registro de pagos SHALL exponerse como un único punto de entrada, de modo que todo origen se registre con la misma estructura de movimiento.

#### Scenario: Pago ingresado por el usuario

- **WHEN** el usuario registra un pago desde la interfaz
- **THEN** el movimiento queda persistido con origen `manual`

#### Scenario: Pago originado por el proveedor

- **WHEN** se registra un pago a partir de un aviso del proveedor o de una reconciliación
- **THEN** el movimiento queda persistido con el origen que identifica a ese proveedor
- **AND** conserva la misma estructura que un movimiento manual

#### Scenario: Origen visible en el historial

- **WHEN** el usuario consulta el historial de pagos de una factura
- **THEN** cada movimiento muestra su origen

## ADDED Requirements

### Requirement: Referencia externa única del movimiento

Un movimiento originado en un proveedor externo SHALL registrar la referencia con la que ese proveedor lo identifica. La combinación de proveedor y referencia externa SHALL ser única entre todos los movimientos. Los movimientos manuales NO SHALL requerir referencia externa.

#### Scenario: Segundo intento de registrar el mismo pago externo

- **WHEN** se intenta registrar un movimiento con un proveedor y una referencia externa que ya existen
- **THEN** no se crea un movimiento adicional
- **AND** el saldo de la factura no cambia

#### Scenario: Movimientos manuales sin referencia

- **WHEN** el usuario registra varios pagos manuales
- **THEN** todos quedan persistidos sin referencia externa
- **AND** la ausencia de referencia no impide registrar movimientos manuales sucesivos

### Requirement: Reembolsos y contracargos del proveedor

Un reembolso o contracargo informado por el proveedor SHALL registrarse como un movimiento negativo independiente, con su propia referencia externa, y NO SHALL modelarse como reversión de otro movimiento. El sistema SHALL admitir varios reembolsos sobre un mismo pago, incluyendo reembolsos por montos menores al pago original. La reversión de movimientos desde la interfaz queda reservada para deshacer un movimiento completo registrado por una persona.

#### Scenario: Reembolso total

- **WHEN** el proveedor informa un reembolso por el monto completo de un pago ya registrado
- **THEN** se registra un movimiento negativo por ese monto con su propia referencia externa
- **AND** el saldo de la factura vuelve al valor previo a ese pago
- **AND** el movimiento del pago original permanece en el historial

#### Scenario: Reembolsos parciales sucesivos

- **WHEN** el proveedor informa dos reembolsos parciales distintos sobre un mismo pago
- **THEN** ambos quedan registrados como movimientos negativos independientes
- **AND** el saldo de la factura refleja la suma de ambos

#### Scenario: Reembolso repetido

- **WHEN** llega dos veces el aviso de un mismo reembolso
- **THEN** se registra un único movimiento negativo
