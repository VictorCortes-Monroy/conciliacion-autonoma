## MODIFIED Requirements

### Requirement: Listado de facturas

El sistema SHALL exponer un listado de todas las facturas mostrando, para cada una, su cliente, monto, fecha de vencimiento, saldo, estado de cobro, condición de vencimiento y su modo de cobro.

#### Scenario: Consulta del listado

- **WHEN** el usuario abre la vista de listado
- **THEN** se muestran todas las facturas registradas con su saldo, estado de cobro y condición de vencimiento calculados al momento de la consulta

#### Scenario: Modo de cobro visible

- **WHEN** el listado incluye facturas en modo cobro por link y facturas que no lo están
- **THEN** cada factura muestra en cuál de los dos modos se encuentra
