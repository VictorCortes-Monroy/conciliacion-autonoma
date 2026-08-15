## Purpose

Permitir que una factura se cobre mediante un link de pago del proveedor, administrando su vigencia, su regeneración cuando expira y el modo de cobro de la factura, de modo que el pago llegue ya identificado y no haya que adivinar a qué factura corresponde.

## ADDED Requirements

### Requirement: Modo de cobro de la factura

Cada factura SHALL tener un modo de cobro por link, apagado por omisión. El usuario SHALL poder encenderlo y apagarlo en cualquier momento mientras la factura tenga saldo pendiente. Con el modo encendido, la factura SHALL mantener un link de pago vigente.

#### Scenario: Encender el cobro por link

- **WHEN** el usuario enciende el cobro por link en una factura con saldo pendiente
- **THEN** se genera un link de pago para esa factura
- **AND** la factura queda en modo cobro por link

#### Scenario: Apagar el cobro por link

- **WHEN** el usuario apaga el cobro por link en una factura
- **THEN** el link vigente queda cancelado
- **AND** la factura deja de tener links vigentes
- **AND** no se generan links nuevos para esa factura hasta que el modo se encienda otra vez

#### Scenario: Estado visible en el detalle

- **WHEN** el usuario consulta el detalle de una factura
- **THEN** se muestra si está en modo cobro por link y, en ese caso, la URL del link vigente

### Requirement: Generación del link de pago

Al generar un link, el sistema SHALL crear en el proveedor una intención de cobro por el saldo pendiente de la factura, identificando la factura mediante una referencia propia que el proveedor devolverá en sus avisos. El sistema SHALL persistir la URL del link, la referencia enviada, el identificador que el proveedor asigne y el momento de creación.

#### Scenario: Link generado

- **WHEN** el sistema genera un link para una factura
- **THEN** el link queda persistido con su URL, su referencia, el identificador del proveedor y su fecha de creación
- **AND** el monto de la intención de cobro corresponde al saldo pendiente de la factura al momento de generarla

#### Scenario: El proveedor no responde

- **WHEN** el proveedor no puede generar la intención de cobro
- **THEN** no se persiste ningún link
- **AND** el sistema informa que el link no pudo generarse
- **AND** el modo de cobro de la factura no cambia

### Requirement: Vigencia y regeneración del link

Un link SHALL tener uno de estos estados: vigente, expirado, pagado o cancelado. Una factura SHALL tener a lo sumo un link vigente a la vez y SHALL conservar el historial de todos sus links. Cuando se consulte el link de una factura en modo cobro por link y el link vigente esté expirado, el sistema SHALL generar uno nuevo en ese momento. El sistema NO SHALL regenerar links mediante un proceso periódico.

#### Scenario: Link expirado se regenera al consultarlo

- **WHEN** el usuario consulta el link de una factura en modo cobro por link cuyo link anterior expiró
- **THEN** se genera un link nuevo y se expone su URL
- **AND** el link anterior permanece en el historial con estado expirado

#### Scenario: Link vigente no se regenera

- **WHEN** el usuario consulta el link de una factura cuyo link vigente no ha expirado
- **THEN** se expone la URL del link existente sin generar uno nuevo

#### Scenario: Un solo link vigente

- **WHEN** una factura tiene un link vigente y se genera uno nuevo
- **THEN** el link anterior deja de estar vigente
- **AND** solo el link nuevo queda vigente

#### Scenario: Link pagado

- **WHEN** se registra un pago proveniente de un link
- **THEN** ese link queda en estado pagado
- **AND** deja de considerarse vigente

### Requirement: El link identifica a la factura

La referencia enviada al proveedor SHALL identificar sin ambigüedad a una única factura, de modo que un pago recibido pueda asociarse a su factura sin heurísticas de monto, fecha o nombre de cliente.

#### Scenario: Aviso de pago asociado a su factura

- **WHEN** llega un pago del proveedor cuya referencia corresponde a un link generado por el sistema
- **THEN** el pago se asocia a la factura de ese link sin recurrir a coincidencias por monto, fecha ni cliente
