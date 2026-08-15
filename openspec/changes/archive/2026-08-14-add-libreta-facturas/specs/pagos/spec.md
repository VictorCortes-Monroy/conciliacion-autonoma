## Purpose

Registrar los movimientos de pago que se aplican contra una factura, incluyendo abonos parciales y reversiones, como un historial inmutable que sirve de rastro de auditoría de todo cambio en el saldo.

## ADDED Requirements

### Requirement: Registro de pagos

El sistema SHALL permitir registrar un pago contra una factura existente, con monto, fecha del pago y origen. El monto SHALL ser distinto de cero. El pago SHALL quedar asociado a una única factura.

#### Scenario: Pago registrado correctamente

- **WHEN** el usuario registra un pago con monto distinto de cero contra una factura existente
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

### Requirement: Pagos parciales

El sistema SHALL aceptar pagos cuyo monto sea menor al saldo pendiente de la factura, y SHALL aceptar múltiples pagos sucesivos contra una misma factura.

#### Scenario: Abono menor al saldo

- **WHEN** el usuario registra un pago de monto menor al saldo pendiente de la factura
- **THEN** el pago se acepta
- **AND** la factura queda con saldo positivo y estado de cobro `PARCIAL`

#### Scenario: Varios abonos sucesivos

- **WHEN** el usuario registra varios pagos parciales contra la misma factura
- **THEN** todos los movimientos quedan persistidos por separado
- **AND** el saldo refleja la suma de todos ellos

### Requirement: Monto sugerido al registrar un pago

Al iniciar el registro de un pago sobre una factura, el sistema SHALL proponer como monto el saldo pendiente de esa factura, permitiendo al usuario modificarlo antes de confirmar.

#### Scenario: Pago total en un paso

- **WHEN** el usuario inicia el registro de un pago sobre una factura con saldo pendiente y confirma sin modificar el monto propuesto
- **THEN** se registra un pago por el saldo completo
- **AND** la factura queda con estado de cobro `PAGADA`

#### Scenario: El usuario ajusta el monto

- **WHEN** el usuario inicia el registro de un pago y reemplaza el monto propuesto por uno menor
- **THEN** se registra un pago por el monto ingresado por el usuario

### Requirement: Sobrepago permitido

El sistema SHALL aceptar pagos que hagan que la suma de movimientos supere el monto de la factura, sin bloquear la operación, y SHALL dejar el sobrepago visible como saldo negativo.

#### Scenario: Pago que excede el saldo

- **WHEN** el usuario registra un pago de monto mayor al saldo pendiente de la factura
- **THEN** el pago se acepta y queda registrado
- **AND** la factura queda con saldo negativo y estado de cobro `PAGADA`

### Requirement: Reversión de pagos

El sistema SHALL permitir revertir un pago ya registrado. La reversión SHALL efectuarse registrando un nuevo movimiento de monto negativo equivalente, referido a la misma factura. El movimiento original SHALL permanecer visible en el historial.

#### Scenario: Reversión de un pago

- **WHEN** el usuario revierte un pago previamente registrado
- **THEN** se agrega un nuevo movimiento de monto negativo por el mismo valor contra la misma factura
- **AND** el movimiento original sigue apareciendo en el historial de la factura
- **AND** el saldo de la factura vuelve al valor previo a ese pago

### Requirement: Historial inmutable

El sistema NO SHALL permitir modificar ni eliminar un movimiento de pago ya registrado. Toda corrección SHALL expresarse como un movimiento adicional. El historial completo de una factura SHALL permanecer consultable.

#### Scenario: Intento de edición

- **WHEN** se intenta modificar el monto o la fecha de un movimiento de pago ya registrado
- **THEN** la operación no está disponible y el movimiento permanece inalterado

#### Scenario: Historial completo tras una reversión

- **WHEN** se consulta el historial de una factura cuyo pago fue revertido
- **THEN** se muestran ambos movimientos: el pago original y su reversión negativa

### Requirement: Origen del pago identificado

Todo movimiento de pago SHALL registrar su origen. En esta etapa, todo movimiento ingresado desde la interfaz SHALL quedar con origen `manual`. El registro de pagos SHALL exponerse como un único punto de entrada, de modo que orígenes futuros se registren con la misma estructura de movimiento.

#### Scenario: Pago ingresado por el usuario

- **WHEN** el usuario registra un pago desde la interfaz
- **THEN** el movimiento queda persistido con origen `manual`

#### Scenario: Origen visible en el historial

- **WHEN** el usuario consulta el historial de pagos de una factura
- **THEN** cada movimiento muestra su origen
