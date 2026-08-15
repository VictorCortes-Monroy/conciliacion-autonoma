## Purpose

Registrar las facturas emitidas y exponer, sin intervención manual, cuánto se debe de cada una: su saldo, su estado de cobro y si está vencida. Es la fuente de verdad sobre quién debe, cuánto y desde cuándo.

## Requirements

### Requirement: Creación de facturas

El sistema SHALL permitir registrar una factura con cliente, monto y fecha de vencimiento. El monto SHALL ser estrictamente mayor a cero. Los tres campos SHALL ser obligatorios.

#### Scenario: Factura registrada correctamente

- **WHEN** el usuario registra una factura con cliente, monto mayor a cero y fecha de vencimiento
- **THEN** la factura queda persistida con un identificador propio y su fecha de creación
- **AND** su saldo inicial es igual a su monto

#### Scenario: Monto inválido

- **WHEN** el usuario intenta registrar una factura con monto igual o menor a cero
- **THEN** el sistema rechaza la operación e informa que el monto debe ser mayor a cero
- **AND** no se crea ninguna factura

#### Scenario: Campo obligatorio ausente

- **WHEN** el usuario intenta registrar una factura sin cliente, sin monto o sin fecha de vencimiento
- **THEN** el sistema rechaza la operación e informa cuál campo falta
- **AND** no se crea ninguna factura

### Requirement: Representación exacta de montos

El sistema SHALL representar y operar todos los montos sin pérdida de precisión. Ninguna suma de pagos SHALL producir un saldo con error de redondeo.

#### Scenario: Suma de pagos sin error de redondeo

- **WHEN** una factura de un monto dado recibe pagos parciales cuya suma es exactamente igual a ese monto
- **THEN** el saldo resultante es exactamente cero
- **AND** la factura se considera pagada

### Requirement: Saldo derivado

El sistema SHALL calcular el saldo de una factura como su monto menos la suma de todos los pagos registrados contra ella. El saldo SHALL derivarse en el momento de la consulta y no SHALL almacenarse como dato editable.

#### Scenario: Saldo sin pagos

- **WHEN** se consulta una factura que no tiene pagos registrados
- **THEN** su saldo es igual a su monto

#### Scenario: Saldo tras un pago parcial

- **WHEN** una factura de monto M tiene un pago registrado de monto P, con P menor a M
- **THEN** su saldo es M menos P

#### Scenario: El saldo refleja inmediatamente un pago revertido

- **WHEN** se revierte un pago previamente registrado contra una factura
- **THEN** el saldo de la factura vuelve al valor que tenía antes de ese pago

### Requirement: Estado de cobro derivado

El sistema SHALL exponer un estado de cobro por factura, derivado exclusivamente de su saldo: `PENDIENTE` cuando el saldo es igual al monto, `PARCIAL` cuando el saldo es mayor a cero y menor al monto, y `PAGADA` cuando el saldo es igual o menor a cero. El estado NO SHALL almacenarse ni poder editarse directamente.

#### Scenario: Factura sin pagos

- **WHEN** se consulta una factura cuyo saldo es igual a su monto
- **THEN** su estado de cobro es `PENDIENTE`

#### Scenario: Factura con abono parcial

- **WHEN** se consulta una factura cuyo saldo es mayor a cero y menor a su monto
- **THEN** su estado de cobro es `PARCIAL`

#### Scenario: Factura cubierta por completo

- **WHEN** se consulta una factura cuyo saldo es cero
- **THEN** su estado de cobro es `PAGADA`

#### Scenario: Factura sobrepagada

- **WHEN** la suma de pagos de una factura supera su monto
- **THEN** su estado de cobro es `PAGADA`
- **AND** su saldo se expone como valor negativo

### Requirement: Condición de vencimiento derivada

El sistema SHALL exponer si una factura está vencida, entendiendo por vencida que su fecha de vencimiento es anterior a la fecha actual y su saldo es mayor a cero. La condición de vencimiento SHALL ser independiente del estado de cobro: una misma factura puede estar `PARCIAL` y vencida simultáneamente. El sistema SHALL exponer también los días transcurridos desde el vencimiento.

#### Scenario: Factura impaga pasada su fecha

- **WHEN** se consulta una factura con saldo mayor a cero cuya fecha de vencimiento es anterior a la fecha actual
- **THEN** se expone como vencida
- **AND** se expone la cantidad de días transcurridos desde su fecha de vencimiento

#### Scenario: Factura parcial y vencida a la vez

- **WHEN** se consulta una factura con estado de cobro `PARCIAL` cuya fecha de vencimiento ya pasó
- **THEN** se expone como `PARCIAL` y como vencida al mismo tiempo

#### Scenario: Factura pagada después de su vencimiento

- **WHEN** una factura cuya fecha de vencimiento ya pasó recibe pagos que dejan su saldo en cero
- **THEN** deja de exponerse como vencida

#### Scenario: Factura vigente

- **WHEN** se consulta una factura cuya fecha de vencimiento es igual o posterior a la fecha actual
- **THEN** no se expone como vencida, independientemente de su saldo

### Requirement: Listado de facturas

El sistema SHALL exponer un listado de todas las facturas mostrando, para cada una, su cliente, monto, fecha de vencimiento, saldo, estado de cobro, condición de vencimiento y su modo de cobro.

#### Scenario: Consulta del listado

- **WHEN** el usuario abre la vista de listado
- **THEN** se muestran todas las facturas registradas con su saldo, estado de cobro y condición de vencimiento calculados al momento de la consulta

#### Scenario: Modo de cobro visible

- **WHEN** el listado incluye facturas en modo cobro por link y facturas que no lo están
- **THEN** cada factura muestra en cuál de los dos modos se encuentra

### Requirement: Detalle de factura

El sistema SHALL exponer el detalle de una factura individual, incluyendo sus datos de registro, su saldo, su estado de cobro, su condición de vencimiento y el historial completo de pagos registrados contra ella.

#### Scenario: Consulta del detalle

- **WHEN** el usuario abre el detalle de una factura
- **THEN** se muestran sus datos de registro, su saldo actual, su estado de cobro, su condición de vencimiento y todos los movimientos de pago asociados en orden cronológico

### Requirement: Vista agregada de deuda

El sistema SHALL exponer, derivado de las facturas registradas: el total adeudado, el total vencido, y la deuda agrupada por cliente. Ninguno de estos agregados SHALL almacenarse.

#### Scenario: Total adeudado

- **WHEN** el usuario consulta la vista agregada
- **THEN** se muestra la suma de los saldos positivos de todas las facturas

#### Scenario: Total vencido

- **WHEN** el usuario consulta la vista agregada
- **THEN** se muestra la suma de los saldos de las facturas expuestas como vencidas

#### Scenario: Deuda por cliente

- **WHEN** el usuario consulta la vista agregada
- **THEN** se muestra, por cada cliente con saldo positivo, el total que adeuda
