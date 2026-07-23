# Upgrades and compatibility

0.4 is a deliberate pre-deployment clean-state reset. It recognizes neither
pre-0.4 development schemas nor their configuration or commands. There is no
migration, import, conversion, preservation, or tailored compatibility path.

To adopt this checkpoint, retain any older development database only as an
offline historical artifact and supply an absent path for the new report
database. Do not point the 0.4 binary at an older database: it fails closed and
does not rewrite it.

Any future schema change must publish its own source/target versions, backup
requirements, validation steps, and rollback limits. That future obligation
does not create a migration promise for the retired development schemas.
